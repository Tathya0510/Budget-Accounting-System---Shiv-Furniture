from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from .models import AnalyticalAccount, Budget, BudgetPeriod, Contact, Document, DocumentLine


class BudgetActualsTests(TestCase):
    def test_budget_actuals_from_posted_vendor_bill_lines(self):
        analytic = AnalyticalAccount.objects.create(name="Workshop", code="WS")
        period = BudgetPeriod.objects.create(name="Jan 2026", start_date="2026-01-01", end_date="2026-01-31")
        budget = Budget.objects.create(analytic_account=analytic, period=period, kind=Budget.Kind.EXPENSE, amount=1000)

        vendor = Contact.objects.create(name="Vendor A", contact_type=Contact.Type.VENDOR)
        bill = Document.objects.create(doc_type=Document.Type.VENDOR_BILL, contact=vendor, issue_date="2026-01-15")
        DocumentLine.objects.create(
            document=bill, description="Wood", quantity=1, unit_price=Decimal("250.00"), analytic_account=analytic
        )
        bill.post()

        self.assertEqual(budget.actual_amount, Decimal("250.00"))


class PortalSecurityTests(TestCase):
    def test_portal_user_cannot_view_other_contact_document(self):
        user1 = User.objects.create_user(username="p1", password="pw")
        contact1 = Contact.objects.create(name="C1", contact_type=Contact.Type.CUSTOMER, user=user1)
        user2 = User.objects.create_user(username="p2", password="pw")
        contact2 = Contact.objects.create(name="C2", contact_type=Contact.Type.CUSTOMER, user=user2)

        doc = Document.objects.create(doc_type=Document.Type.CUSTOMER_INVOICE, contact=contact2)

        c = Client()
        c.login(username="p1", password="pw")
        resp = c.get(reverse("portal_document_detail", kwargs={"number": doc.number}))
        self.assertEqual(resp.status_code, 404)


class ContactAdminTests(TestCase):
    def test_contact_admin_has_document_inline(self):
        from django.contrib.admin.sites import site
        from .admin import ContactAdmin
        from .models import Contact

        contact_admin = ContactAdmin(Contact, site)
        from .admin import DocumentInline
        self.assertTrue(any(isinstance(inline, DocumentInline) for inline in contact_admin.get_inline_instances(None)))


class PortalDashboardTests(TestCase):
    def test_dashboard_shows_correct_counts_and_invoice_rows(self):
        user = User.objects.create_user(username="p1", password="pw")
        contact = Contact.objects.create(name="C1", contact_type=Contact.Type.CUSTOMER, user=user)
        
        # Create 15 invoices
        for i in range(15):
            Document.objects.create(
                doc_type=Document.Type.CUSTOMER_INVOICE, 
                contact=contact, 
                number=f"INV-{i}",
                total_amount=Decimal("100.00")
            )
        
        # Create 5 bills
        for i in range(5):
            Document.objects.create(
                doc_type=Document.Type.VENDOR_BILL, 
                contact=contact, 
                number=f"BILL-{i}",
                total_amount=Decimal("50.00")
            )

        c = Client()
        c.login(username="p1", password="pw")
        resp = c.get(reverse("portal_dashboard"))
        
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["invoice_count"], 15)
        self.assertEqual(resp.context["bill_count"], 5)
        self.assertEqual(len(resp.context["invoice_rows"]), 10)
        self.assertEqual(len(resp.context["recent_docs"]), 10)


class PortalDocumentsTests(TestCase):
    def test_documents_page_includes_invoice_rows(self):
        user = User.objects.create_user(username="p2", password="pw")
        contact = Contact.objects.create(name="C2", contact_type=Contact.Type.CUSTOMER, user=user)
        for i in range(3):
            Document.objects.create(
                doc_type=Document.Type.CUSTOMER_INVOICE,
                contact=contact,
                number=f"INV-DOC-{i}",
                total_amount=Decimal("100.00"),
            )
        c = Client()
        c.login(username="p2", password="pw")
        resp = c.get(reverse("portal_documents"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["invoice_count"], 3)
        self.assertEqual(len(resp.context["invoice_rows"]), 3)


class CustomerLoginTests(TestCase):
    def test_customer_login_rejects_staff_user(self):
        staff = User.objects.create_user(username="staff1", password="pw")
        staff.is_staff = True
        staff.save(update_fields=["is_staff"])
        c = Client()
        resp = c.post(
            reverse("customer_login"),
            {"username": "staff1", "password": "pw"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Staff accounts must use the admin login.")
