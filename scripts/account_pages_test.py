"""Read-only account regression: routing, ownership, paging and DAO parameters."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
from contextlib import contextmanager
from unittest.mock import patch, Mock
from decimal import Decimal
from app import create_app
from app.services import member_service
from app.dao import member

@contextmanager
def cursor(*args,**kwargs):
    yield Mock()

class AccountTests(unittest.TestCase):
    def setUp(self):
        self.app=create_app(); self.client=self.app.test_client()
    def login(self,role="customer",mid="C0000001"):
        with self.client.session_transaction() as s:
            s['role']=role; s['member_id']=mid
    def test_requires_login(self):
        self.assertEqual(self.client.get('/account').status_code,401)
    def test_employee_rejected(self):
        self.login('clerk'); self.assertEqual(self.client.get('/account').status_code,403)
    def test_missing_binding(self):
        self.login(mid=None); self.assertEqual(self.client.get('/account').status_code,409)
    def test_bad_paging(self):
        self.login(); self.assertEqual(self.client.get('/account?points_offset=bad').status_code,400)
    def test_identity_and_paging(self):
        self.login()
        account={'member_id':'C0000001','points':18,'balance':Decimal('77.00')}
        def logs(cur,mid,**kw):
            self.assertEqual(mid,'C0000001')
            if kw['kind']=='points':
                self.assertEqual(kw['offset'],20)
                return [{'point_type':'REVERSAL','points':3,'order_id':None,'created_at':None}]*21
            return []
        with patch.object(member_service,'db_cursor',cursor), patch.object(member,'get_member',return_value=account), patch.object(member,'list_account_logs',side_effect=logs):
            r=self.client.get('/account?member_id=C0000002&points_offset=20')
        self.assertEqual(r.status_code,200)
        body=r.get_data(as_text=True)
        for value in ['77.00','-3','积分下一页','暂无储值流水','我的账户']: self.assertIn(value,body)
        self.assertNotIn('会员编号：C0000002',body)
    def test_dao_filters_member_and_stable_order(self):
        cur=Mock(); cur.description=[('point_type',)]; cur.fetchall.return_value=[]
        member.list_account_logs(cur,'C0000001',kind='points',offset=20)
        args=cur.execute.call_args.args
        self.assertIn('WHERE member_id = ?',args[0]); self.assertIn('point_log_id DESC',args[0])
        self.assertEqual(args[1:],('C0000001',20,21))

if __name__=='__main__': unittest.main(verbosity=2)
