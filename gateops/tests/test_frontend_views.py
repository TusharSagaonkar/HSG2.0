"""Server-rendered GateOps frontend tests for selected-society scoping."""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from gateops.models import Gate, GateOpsAuditLog, MasterSettings, Rule, RuleAction, RuleCondition, RuleEvaluation, VisitorCategory
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from housing_accounting.users.tests.factories import UserFactory
from societies.services import create_society


class GateOpsFrontendViewTest(TestCase):
    """Focused coverage for selected-society GateOps frontend behavior."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory(password="password")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def _create_accessible_society(self, name):
        return create_society(user=self.user, name=name)

    def _select_society(self, society):
        session = self.client.session
        session[SESSION_SELECTED_SOCIETY_ID] = society.id
        session.save()

    def test_missing_selected_society_returns_prominent_error_without_fallback(self):
        response = self.client.get(reverse("gateops:dashboard"))

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "No society selected", status_code=404)
        self.assertContains(response, "Select a society to use Gate Operations.", status_code=404)

    def test_dashboard_renders_bootstrap_sections_for_selected_society(self):
        society = self._create_accessible_society("Alpha Heights")
        self._select_society(society)

        response = self.client.get(reverse("gateops:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha Heights Bootstrap Completeness")
        for label in (
            "Config",
            "Gates",
            "Visitor categories",
            "Vehicle categories",
            "Material categories",
            "Pass types",
            "Approval types",
            "Gate roles",
            "Holiday calendar",
            "Master settings",
        ):
            self.assertContains(response, label)

    def test_dashboard_exposes_gate_management_navigation(self):
        self._select_society(self._create_accessible_society("Alpha Heights"))

        response = self.client.get(reverse("gateops:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gate Management")
        self.assertContains(response, 'href="/gateops/"')
        self.assertContains(response, reverse("gateops:setup-index"))
        self.assertContains(response, reverse("gateops:rule-list"))
        self.assertContains(response, reverse("gateops:rule-test"))
        self.assertContains(response, reverse("gateops:logs"))
        self.assertContains(response, "Setup & Settings")
        self.assertNotContains(response, 'id="gateops-nav"')
        self.assertNotContains(response, 'aria-expanded="true"')

    def test_gate_management_navigation_marks_rules_active_without_submenu(self):
        self._select_society(self._create_accessible_society("Alpha Heights"))

        response = self.client.get(reverse("gateops:rule-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'nav-item active')
        self.assertContains(response, 'href="/gateops/"')
        self.assertContains(response, 'aria-label="Gate Management"')
        self.assertNotContains(response, 'id="gateops-nav"')
        self.assertNotContains(response, 'class="collapse show"')

    def test_rule_create_form_renders_guidance_and_master_data_link(self):
        self._select_society(self._create_accessible_society("Alpha Heights"))

        response = self.client.get(reverse("gateops:rule-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "How rules work")
        self.assertContains(response, "Rule builder checklist")
        self.assertContains(response, "lower numbers run first")
        self.assertContains(response, reverse("gateops:setup-index"))

    def test_rule_create_post_creates_selected_society_scoped_rule_and_audit_log(self):
        society = self._create_accessible_society("Alpha Heights")
        other_society = self._create_accessible_society("Beta Heights")
        self._select_society(society)

        response = self.client.post(
            reverse("gateops:rule-create"),
            data={
                "name": "Delivery Auto Approve",
                "code": "delivery_auto_approve",
                "description": "Allow known delivery visitors.",
                "priority": "10",
                "is_active": "on",
                "valid_from": timezone.localdate().isoformat(),
                "valid_until": "",
                "applies_on": Rule.AppliesOn.ENTRY,
                "visitor_category": "",
                "vehicle_category": "",
                "material_category": "",
                "gate": "",
            },
        )

        rule = Rule.objects.get(code="DELIVERY_AUTO_APPROVE")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("gateops:rule-detail", kwargs={"pk": rule.pk}))
        self.assertEqual(rule.society, society)
        self.assertNotEqual(rule.society, other_society)
        self.assertTrue(
            GateOpsAuditLog.objects.filter(
                society=society,
                action=GateOpsAuditLog.Action.CREATE,
                entity_type="Rule",
                entity_id=str(rule.pk),
            ).exists()
        )

    def test_rule_list_scope_changes_when_selected_society_changes(self):
        alpha = self._create_accessible_society("Alpha Heights")
        beta = self._create_accessible_society("Beta Heights")
        alpha_rule = Rule.objects.create(
            society=alpha,
            name="Alpha Rule",
            code="ALPHA_RULE",
            priority=10,
            applies_on=Rule.AppliesOn.ENTRY,
        )
        beta_rule = Rule.objects.create(
            society=beta,
            name="Beta Rule",
            code="BETA_RULE",
            priority=10,
            applies_on=Rule.AppliesOn.ENTRY,
        )

        self._select_society(alpha)
        alpha_response = self.client.get(reverse("gateops:rule-list"))
        self._select_society(beta)
        beta_response = self.client.get(reverse("gateops:rule-list"))

        self.assertEqual(alpha_response.status_code, 200)
        self.assertContains(alpha_response, alpha_rule.code)
        self.assertContains(alpha_response, "Alpha Heights")
        self.assertNotContains(alpha_response, beta_rule.code)
        self.assertEqual(beta_response.status_code, 200)
        self.assertContains(beta_response, beta_rule.code)
        self.assertContains(beta_response, "Beta Heights")
        self.assertNotContains(beta_response, alpha_rule.code)

    def test_rule_detail_rejects_rule_from_unselected_society(self):
        alpha = self._create_accessible_society("Alpha Heights")
        beta = self._create_accessible_society("Beta Heights")
        beta_rule = Rule.objects.create(
            society=beta,
            name="Beta Rule",
            code="BETA_RULE",
            priority=10,
            applies_on=Rule.AppliesOn.ENTRY,
        )
        self._select_society(alpha)

        response = self.client.get(reverse("gateops:rule-detail", kwargs={"pk": beta_rule.pk}))

        self.assertEqual(response.status_code, 404)

    def test_setup_index_manage_links_target_accessible_sections(self):
        alpha = self._create_accessible_society("Alpha Heights")
        beta = self._create_accessible_society("Beta Heights")
        alpha_gate = Gate.objects.create(society=alpha, name="North Gate", code="NORTH", gate_type=Gate.GateType.MAIN)
        beta_gate = Gate.objects.create(society=beta, name="Beta Private Gate", code="BETA", gate_type=Gate.GateType.SERVICE)
        self._select_society(alpha)

        index_response = self.client.get(reverse("gateops:setup-index"))
        gate_section_url = reverse("gateops:setup-section", kwargs={"slug": "gates"})
        master_settings_url = reverse("gateops:setup-section", kwargs={"slug": "master-settings"})
        section_response = self.client.get(gate_section_url)
        htmx_section_response = self.client.get(gate_section_url, HTTP_HX_REQUEST="true")

        self.assertEqual(index_response.status_code, 200)
        self.assertContains(index_response, "Gates")
        self.assertContains(index_response, "Master Settings")
        self.assertContains(index_response, f'href="{gate_section_url}"')
        self.assertContains(index_response, f'hx-get="{gate_section_url}"')
        self.assertContains(index_response, f'href="{master_settings_url}"')
        self.assertContains(index_response, f'hx-get="{master_settings_url}"')
        self.assertContains(index_response, 'hx-target="#workspace"')
        self.assertContains(index_response, 'hx-select="#workspace"')
        self.assertContains(index_response, 'hx-push-url="true"')
        self.assertEqual(section_response.status_code, 200)
        self.assertEqual(htmx_section_response.status_code, 200)
        self.assertContains(section_response, alpha_gate.code)
        self.assertNotContains(section_response, beta_gate.code)

    def test_setup_gate_create_update_and_deactivate_are_selected_society_scoped(self):
        alpha = self._create_accessible_society("Alpha Heights")
        beta = self._create_accessible_society("Beta Heights")
        self._select_society(alpha)

        create_response = self.client.post(
            reverse("gateops:setup-create", kwargs={"slug": "gates"}),
            data={
                "name": "Service Gate",
                "code": "service",
                "gate_type": Gate.GateType.SERVICE,
                "gps_lat": "",
                "gps_lng": "",
                "is_active": "on",
            },
        )

        gate = Gate.objects.get(society=alpha, code="SERVICE")
        self.assertEqual(create_response.status_code, 302)
        self.assertFalse(Gate.objects.filter(society=beta, code="SERVICE").exists())
        self.assertTrue(
            GateOpsAuditLog.objects.filter(
                society=alpha,
                action=GateOpsAuditLog.Action.CREATE,
                entity_type="Gate",
                entity_id=str(gate.pk),
            ).exists()
        )

        edit_response = self.client.post(
            reverse("gateops:setup-edit", kwargs={"slug": "gates", "pk": gate.pk}),
            data={
                "name": "Service Gate Updated",
                "code": "SERVICE",
                "gate_type": Gate.GateType.PEDESTRIAN,
                "gps_lat": "",
                "gps_lng": "",
                "is_active": "on",
            },
        )
        gate.refresh_from_db()
        self.assertEqual(edit_response.status_code, 302)
        self.assertEqual(gate.name, "Service Gate Updated")
        self.assertEqual(gate.gate_type, Gate.GateType.PEDESTRIAN)

        delete_response = self.client.post(reverse("gateops:setup-delete", kwargs={"slug": "gates", "pk": gate.pk}))
        gate.refresh_from_db()
        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(gate.is_active)
        self.assertIsNotNone(gate.deleted_at)

    def test_setup_rejects_cross_society_gate_edit(self):
        alpha = self._create_accessible_society("Alpha Heights")
        beta = self._create_accessible_society("Beta Heights")
        beta_gate = Gate.objects.create(society=beta, name="Beta Gate", code="BETA", gate_type=Gate.GateType.MAIN)
        self._select_society(alpha)

        response = self.client.get(reverse("gateops:setup-edit", kwargs={"slug": "gates", "pk": beta_gate.pk}))

        self.assertEqual(response.status_code, 404)

    def test_master_settings_singleton_update_is_selected_society_scoped(self):
        alpha = self._create_accessible_society("Alpha Heights")
        beta = self._create_accessible_society("Beta Heights")
        alpha_settings = MasterSettings.objects.get(society=alpha)
        beta_settings = MasterSettings.objects.get(society=beta)
        beta_settings.settings = {"default_language": "fr"}
        beta_settings.save(update_fields=["settings"])
        self._select_society(alpha)

        response = self.client.post(
            reverse("gateops:setup-edit", kwargs={"slug": "master-settings", "pk": alpha_settings.pk}),
            data={"settings_text": '{"default_language": "en", "enable_face_match": true}'},
        )

        alpha_settings.refresh_from_db()
        beta_settings.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(alpha_settings.settings["default_language"], "en")
        self.assertTrue(alpha_settings.settings["enable_face_match"])
        self.assertEqual(alpha_settings.updated_by, self.user)
        self.assertEqual(beta_settings.settings, {"default_language": "fr"})

    def test_logs_are_limited_to_selected_society(self):
        alpha = self._create_accessible_society("Alpha Heights")
        beta = self._create_accessible_society("Beta Heights")
        alpha_rule = Rule.objects.create(
            society=alpha,
            name="Alpha Rule",
            code="ALPHA_RULE",
            priority=10,
            applies_on=Rule.AppliesOn.ENTRY,
        )
        beta_rule = Rule.objects.create(
            society=beta,
            name="Beta Rule",
            code="BETA_RULE",
            priority=10,
            applies_on=Rule.AppliesOn.ENTRY,
        )
        RuleEvaluation.objects.create(
            society=alpha,
            rule=alpha_rule,
            input_context={"marker": "alpha-only"},
            action_taken=RuleEvaluation.ActionTaken.NO_MATCH,
        )
        RuleEvaluation.objects.create(
            society=beta,
            rule=beta_rule,
            input_context={"marker": "beta-only"},
            action_taken=RuleEvaluation.ActionTaken.NO_MATCH,
        )
        GateOpsAuditLog.log(
            society=alpha,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="Rule",
            entity_id=alpha_rule.pk,
            after_value={"marker": "alpha-audit"},
        )
        GateOpsAuditLog.log(
            society=beta,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="Rule",
            entity_id=beta_rule.pk,
            after_value={"marker": "beta-audit"},
        )
        self._select_society(alpha)

        response = self.client.get(reverse("gateops:logs"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, alpha_rule.code)
        self.assertContains(response, f"Rule:{alpha_rule.pk}")
        self.assertNotContains(response, beta_rule.code)
        self.assertNotContains(response, f"Rule:{beta_rule.pk}")

    def test_rule_test_post_uses_real_engine_and_persists_latest_evaluation(self):
        society = self._create_accessible_society("Alpha Heights")
        self._select_society(society)
        visitor_category = VisitorCategory.objects.get(society=society, code="DELIVERY")
        rule = Rule.objects.create(
            society=society,
            name="Delivery Direct Entry",
            code="DELIVERY_DIRECT_ENTRY",
            priority=5,
            applies_on=Rule.AppliesOn.ENTRY,
        )
        RuleCondition.objects.create(
            rule=rule,
            field=RuleCondition.ConditionField.VISITOR_TYPE,
            operator=RuleCondition.Operator.EQ,
            value="DELIVERY",
        )
        RuleAction.objects.create(
            rule=rule,
            action=RuleAction.ActionType.DIRECT_ENTRY,
            execution_order=0,
        )

        response = self.client.post(
            reverse("gateops:rule-test"),
            data={
                "applies_on": Rule.AppliesOn.ENTRY,
                "visitor_category": visitor_category.code,
                "vehicle_category": "",
                "gate": "",
                "date": timezone.localdate().isoformat(),
                "time": "",
                "tower": "A",
                "wing": "1",
                "flat": "101",
                "max_visitors": "",
                "pass_is_valid": "on",
                "extra_context": "{}",
                "rule": str(rule.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rule engine evaluation completed and logged.")
        self.assertContains(response, "Matched")
        self.assertContains(response, "direct_entry")
        evaluation = RuleEvaluation.objects.get(society=society, rule=rule)
        self.assertEqual(evaluation.action_taken, RuleEvaluation.ActionTaken.DIRECT_ENTRY)
        self.assertEqual(evaluation.input_context["visitor_category"], "DELIVERY")
