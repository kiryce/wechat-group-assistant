import json
from types import SimpleNamespace
import pytest
import core
import web_search as web

@pytest.fixture
def enabled(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DATA',tmp_path);core.init_db();core.save_config({'web_enabled':True})

def test_group_question_not_sent_to_search(enabled,monkeypatch):
    monkeypatch.setattr(core,'llm',lambda *a,**k:'{"search":false,"queries":[]}')
    monkeypatch.setattr(web.subprocess,'run',lambda *a,**k:pytest.fail('private group question'))
    assert web.context('我们群明天几点聚会')['status']=='not_needed'

def test_search_results_and_date_reach_context(enabled,monkeypatch):
    monkeypatch.setattr(core,'llm',lambda *a,**k:'{"search":true,"queries":["Jev AI model official"]}')
    def run(*a,**k):
        assert json.loads(k['input'])==['Jev AI model official']
        return SimpleNamespace(returncode=0,stdout=json.dumps([{'title':'Official','url':'https://typesafe.ai/','snippet':'decision model'}]))
    monkeypatch.setattr(web.subprocess,'run',run)
    result=web.context('Jev是什么')
    assert result['status']=='ok' and result['checked_at'] and len(result['results'])==1

def test_query_with_group_or_secret_is_not_searched(enabled,monkeypatch):
    monkeypatch.setattr(core,'llm',lambda *a,**k:json.dumps({'search':True,'queries':['示例群','sk-secret12345678']}))
    monkeypatch.setattr(web.subprocess,'run',lambda *a,**k:pytest.fail('private query'))
    assert web.context('查一下')['status']=='unavailable'

def test_search_failure_is_explicit(enabled,monkeypatch):
    monkeypatch.setattr(core,'llm',lambda *a,**k:'{"search":true,"queries":["Jev"]}')
    monkeypatch.setattr(web.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=''))
    assert web.context('Jev是什么')['status']=='unavailable'

def test_web_fetch_rejects_local_network(monkeypatch):
    monkeypatch.setattr(web.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    assert not web.public_url('https://example.org/')
    assert not web.public_url('file:///private/key')

def test_user_can_disable_search(enabled,monkeypatch):
    monkeypatch.setattr(core,'llm',lambda *a,**k:pytest.fail('must not plan'))
    assert web.context('不要联网，写个排序')['status']=='not_requested'


def test_worker_forces_utf8_in_background_process(enabled,monkeypatch):
    monkeypatch.setattr(core,'llm',lambda *a,**k:'{"search":true,"queries":["Jev AI"]}')
    def run(args,**kwargs):
        assert args[1:3]==['-X','utf8']
        return SimpleNamespace(returncode=0,stdout=json.dumps([{'title':'Unicode: \u2318', 'url':'https://example.com'}]))
    monkeypatch.setattr(web.subprocess,'run',run)
    assert web.context('Jev')['status']=='ok'
