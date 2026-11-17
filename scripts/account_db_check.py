"""Real database read check; no business writes."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from app.config import Config
app=create_app()
for auth in ['trusted','sql']:
    if auth=='sql' and not Config.password_for(Config.login_for('customer')):
        print('sql account page: SKIP (customer login password not configured)')
        continue
    Config.DB_AUTH=auth
    client=app.test_client()
    with client.session_transaction() as s:
        s['username']='customer1'; s['role']='customer'; s['member_id']='C0000001'
    response=client.get('/account?member_id=C0000002')
    assert response.status_code==200,(auth,response.status_code)
    body=response.get_data(as_text=True)
    assert '会员编号：C0000001' in body
    assert '会员编号：C0000002' not in body
    print(auth,'account page: PASS')
