"""pipeline 테스트 — 동기 발송(run_for_subscriber) + 배치 잡(수집→요약→발송).

네트워크·이메일·LLM 없이 동작한다(전부 목/스텁).
실행:  python -m pytest   또는   python -m unittest
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from src import db, pipeline
from src.subscriptions import Subscription

KST = timezone(timedelta(hours=9))


class TestRunForSubscriber(unittest.TestCase):
    def test_no_keywords_does_not_send(self):
        sub = Subscription("a@x.com", keywords=[], send_hour=8, send_minute=0, confirmed=True)
        with mock.patch.object(pipeline.send_email, "send_email") as send:
            pipeline.run_for_subscriber(sub)
        send.assert_not_called()

    def test_no_news_does_not_send(self):
        sub = Subscription("a@x.com", keywords=["주식"], send_hour=8, send_minute=0, confirmed=True)
        with mock.patch.object(pipeline.naver_news, "collect", return_value={"주식": []}), \
             mock.patch.object(pipeline.send_email, "send_email") as send:
            pipeline.run_for_subscriber(sub)
        send.assert_not_called()

    def test_sends_when_news_present(self):
        sub = Subscription("a@x.com", keywords=["주식"], send_hour=8, send_minute=0, confirmed=True)
        news = {"주식": [{"title": "속보", "link": "https://a", "description": "", "published_at": None}]}
        fake_summary = {"주식": [{"headline": "속보", "topic": "T", "topic_summary": "S", "link": "https://a"}]}
        with mock.patch.object(pipeline.naver_news, "collect", return_value=news), \
             mock.patch.object(pipeline.summarizer, "summarize", return_value=fake_summary), \
             mock.patch.object(pipeline.send_email, "send_email") as send:
            pipeline.run_for_subscriber(sub)
        send.assert_called_once()

    def test_unconfirmed_does_not_send(self):
        # 이메일 미확인 구독자는 뉴스가 있어도 발송하지 않는다
        sub = Subscription("a@x.com", keywords=["주식"], send_hour=8, send_minute=0, confirmed=False)
        news = {"주식": [{"title": "속보", "link": "https://a", "description": "", "published_at": None}]}
        with mock.patch.object(pipeline.naver_news, "collect", return_value=news), \
             mock.patch.object(pipeline.send_email, "send_email") as send:
            pipeline.run_for_subscriber(sub)
        send.assert_not_called()


class TestBatchJobs(unittest.TestCase):
    """수집 잡 → 요약 잡 → 발송 잡이 DB를 통해 이어지는지 확인."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)
        db.init_db(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        # 모든 db.* 호출이 임시 DB를 쓰도록 config.DB_PATH를 임시 파일로 교체
        patcher = mock.patch.object(pipeline.config, "DB_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = datetime(2026, 7, 7, 18, 0, 0, tzinfo=KST)

    def _sub(self):
        return Subscription("a@x.com", keywords=["주식"], send_hour=18, send_minute=0, confirmed=True)

    def test_collect_summarize_dispatch_flow(self):
        published = (self.now - timedelta(hours=1)).isoformat()
        news = {"주식": [{"title": "삼성 주가 급등", "link": "https://a/1",
                          "description": "본문", "published_at": published}]}

        # ① 수집 잡: 뉴스 수집(목)해 DB 저장
        with mock.patch.object(pipeline, "load_subscriptions", return_value=[self._sub()]), \
             mock.patch.object(pipeline.naver_news, "collect", return_value=news):
            saved = pipeline.collect_job(now=self.now)
        self.assertEqual(saved, 1)

        # ② 요약 잡: 구독자의 (키워드, 요약 길이, 언어) 조합을 알아야 하므로 load_subscriptions도 목킹.
        # summarizer(LLM 호출)는 네트워크/과금 없이 돌도록 목 처리 — headline/topic/link만 돌려줌
        fake_summary = {"주식": [{"headline": "삼성 주가 급등", "topic": "실적",
                                  "topic_summary": "요약", "link": "https://a/1"}]}
        with mock.patch.object(pipeline, "load_subscriptions", return_value=[self._sub()]), \
             mock.patch.object(pipeline.summarizer, "summarize", return_value=fake_summary):
            created = pipeline.summarize_job(now=self.now)
        self.assertEqual(created, 1)  # (주식, 짧게, 한국어) 조합 1건 → 다이제스트 1개 생성

        # ③ 발송 잡: 18:00 발송 대상에게 DB의 최신 다이제스트를 렌더링해 발송
        with mock.patch.object(pipeline, "load_subscriptions", return_value=[self._sub()]), \
             mock.patch.object(pipeline.send_email, "send_email") as send:
            pipeline.dispatch_job(now=self.now)
        send.assert_called_once()

    def test_collect_job_prunes_stale_articles(self):
        # RECENCY_HOURS(기본 7일)보다 오래된 기사는 수집 잡이 끝날 때 정리된다
        stale = (self.now - timedelta(hours=200)).isoformat()
        db.save_articles({"주식": [{"title": "옛날 기사", "link": "https://a/stale",
                                     "description": "", "published_at": stale}]}, now=self.now, path=self.path)
        with mock.patch.object(pipeline, "load_subscriptions", return_value=[self._sub()]), \
             mock.patch.object(pipeline.naver_news, "collect", return_value={"주식": []}):
            pipeline.collect_job(now=self.now)
        self.assertEqual(db.fetch_articles_for_keyword("주식", now=self.now, hours=999, path=self.path), [])

    def test_dispatch_skips_when_no_recent_digest(self):
        # 다이제스트가 하나도 없으면 발송하지 않는다
        with mock.patch.object(pipeline, "load_subscriptions", return_value=[self._sub()]), \
             mock.patch.object(pipeline.send_email, "send_email") as send:
            pipeline.dispatch_job(now=self.now)
        send.assert_not_called()

    def test_summarize_job_calls_summarizer_per_subscriber_combo(self):
        # 구독자 두 명이 서로 다른 (요약 길이, 언어)를 쓰면, summarizer가 조합마다 따로 호출된다
        published = (self.now - timedelta(hours=1)).isoformat()
        news = {"주식": [{"title": "삼성 주가 급등", "link": "https://a/1",
                          "description": "본문", "published_at": published}]}
        sub_ko = Subscription("a@x.com", keywords=["주식"], send_hour=18, send_minute=0,
                              summary_length="짧게", language="한국어", confirmed=True)
        sub_en = Subscription("b@x.com", keywords=["주식"], send_hour=18, send_minute=0,
                              summary_length="길게", language="영어", confirmed=True)

        with mock.patch.object(pipeline, "load_subscriptions", return_value=[sub_ko]), \
             mock.patch.object(pipeline.naver_news, "collect", return_value=news):
            pipeline.collect_job(now=self.now)

        # summarizer(LLM 호출)는 네트워크/과금 없이 돌도록 목 처리하되, 조합별 결과는 구분해 돌려준다
        def fake_summarize(collected, summary_length, language):
            return {
                kw: [{"headline": "H", "topic": "T", "topic_summary": "S", "link": items[0]["link"]}]
                for kw, items in collected.items() if items
            }

        with mock.patch.object(pipeline, "load_subscriptions", return_value=[sub_ko, sub_en]), \
             mock.patch.object(pipeline.summarizer, "summarize",
                               side_effect=fake_summarize) as spy:
            created = pipeline.summarize_job(now=self.now)

        self.assertEqual(created, 2)  # 조합마다 다이제스트 1개씩, 총 2개
        called_combos = {(c.args[1], c.args[2]) for c in spy.call_args_list}
        self.assertEqual(called_combos, {("짧게", "한국어"), ("길게", "영어")})
        # 조합별로 별도 다이제스트가 남는다 — 서로의 목록엔 안 보임
        ko = db.fetch_digests_for_keywords(["주식"], "짧게", "한국어", now=self.now, path=self.path)
        en = db.fetch_digests_for_keywords(["주식"], "길게", "영어", now=self.now, path=self.path)
        self.assertEqual(len(ko["주식"]), 1)
        self.assertEqual(len(en["주식"]), 1)

    def test_summarize_job_stores_issue_topic_hierarchy(self):
        # 한 키워드에서 이슈 여러 개, 주제당 관련 기사 여러 개가 그대로 다이제스트에 반영되는지 확인
        published = (self.now - timedelta(hours=1)).isoformat()
        news = {"주식": [
            {"title": "삼성 실적 발표", "link": "https://a/1", "description": "", "published_at": published},
            {"title": "삼성 실적 호조", "link": "https://a/2", "description": "", "published_at": published},
            {"title": "금리 인하 기대", "link": "https://a/3", "description": "", "published_at": published},
        ]}
        multi_issue_output = {
            "주식": [
                {"headline": "삼성 실적 이슈", "topic": "실적 발표", "topic_summary": "요약1", "link": "https://a/1"},
                {"headline": "삼성 실적 이슈", "topic": "실적 발표", "topic_summary": "요약1", "link": "https://a/2"},
                {"headline": "금리 이슈", "topic": "인하 기대", "topic_summary": "요약2", "link": "https://a/3"},
            ]
        }

        with mock.patch.object(pipeline, "load_subscriptions", return_value=[self._sub()]), \
             mock.patch.object(pipeline.naver_news, "collect", return_value=news):
            pipeline.collect_job(now=self.now)

        with mock.patch.object(pipeline, "load_subscriptions", return_value=[self._sub()]), \
             mock.patch.object(pipeline.summarizer, "summarize", return_value=multi_issue_output):
            pipeline.summarize_job(now=self.now)

        out = db.fetch_digests_for_keywords(["주식"], "짧게", "한국어", now=self.now, path=self.path)
        issues = out["주식"]
        self.assertEqual([i["headline"] for i in issues], ["삼성 실적 이슈", "금리 이슈"])
        self.assertEqual(issues[0]["topics"][0]["links"], ["https://a/1", "https://a/2"])


if __name__ == "__main__":
    unittest.main()
