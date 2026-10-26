import pytest
import requests
import background_transport as bg
from transport import NotReady,Uncertain

def test_wrong_version_never_sends(monkeypatch):
    monkeypatch.setattr(bg,'status',lambda:dict(compatible=False,version='4.1.9.55'))
    monkeypatch.setattr(bg,'session',lambda:pytest.fail('must not send'))
    with pytest.raises(NotReady): bg.send({'remote_id':'g@chatroom'},'answer')

def test_missing_group_id_never_sends(monkeypatch):
    monkeypatch.setattr(bg,'status',lambda:dict(compatible=True,version='4.1.10.27'))
    monkeypatch.setattr(bg,'session',lambda:pytest.fail('must not send'))
    with pytest.raises(NotReady): bg.send({'remote_id':''},'answer')

def test_timeout_after_post_is_uncertain(monkeypatch):
    monkeypatch.setattr(bg,'status',lambda:dict(compatible=True,version='4.1.10.27'))
    monkeypatch.setattr(bg,'update_allowlist',lambda:None)
    class Session:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def post(self,*a,**kw):raise requests.Timeout()
    monkeypatch.setattr(bg,'session',Session)
    with pytest.raises(Uncertain):bg.send({'remote_id':'g@chatroom'},'answer')
