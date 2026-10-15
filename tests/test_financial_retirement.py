"""Legacy references fail explicitly without erasing their stored history."""
import tempfile
import unittest
from pathlib import Path
from backend.app import create_app
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.retired_capabilities import check_spec
from backend.app.modules.agent_platform.shared_capabilities import resolve_references


class RetirementTests(unittest.TestCase):
    def test_old_frozen_references_fail_before_execution(self):
        for spec in ({'tools':[{'registeredTool':'sfc_history'}]}, {'skills':[{'registeredSkill':'sfc-announcement','content':'old snapshot'}]}):
            with self.assertRaises(GatewayError) as error:check_spec(spec)
            self.assertEqual(error.exception.code,'CAPABILITY_RETIRED')
            with self.assertRaises(GatewayError) as error:resolve_references(spec)
            self.assertEqual(error.exception.code,'CAPABILITY_RETIRED')
        check_spec({'tools':[{'registeredTool':'sfc_news'}],'skills':[]})

    def test_authenticated_api_retirement_and_remaining_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app({'TESTING':True,'DATA_DIR':directory,'CAPABILITY_SKIP_PUBLIC_SEED':True,'GITEE_AI_API_KEY':''})
            try:
                client=app.test_client()
                client.post('/desktop/api/register',json={'email':'retirement@example.invalid','password':'temporary-password'})
                for name in ('sfc_history','sfc_announcements'):
                    response=client.post('/desktop/api/tools/'+name+'/execute',json={'arguments':{'code':'600000'}})
                    self.assertEqual(response.status_code,409,response.json)
                    self.assertEqual(response.json['code'],'CAPABILITY_RETIRED')
                for name in ('sfc-history','sfc-announcement'):
                    response=client.get('/desktop/api/skills/'+name)
                    self.assertEqual(response.status_code,409,response.json)
                root=Path(__file__).resolve().parents[1]
                self.assertTrue((root/'skills/sfc-news/SKILL.md').exists())
                self.assertFalse((root/'skills/sfc-history/SKILL.md').exists())
            finally:app.extensions['workbench_service'].close()
