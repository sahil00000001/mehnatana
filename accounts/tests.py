from django.test import TestCase, override_settings
from django.urls import reverse

from companies.models import Company

from .models import User


class LoginTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code="TEST", legal_name="Test Shop", trade_name="Test Shop",
                                             address_line1="1 Road", city="Bengaluru", state="KA", pincode="560001")
        cls.user = User.objects.create_user(username="9876543210", password="right-password-1", company=cls.company)

    def login(self, password):
        return self.client.post(reverse("login"), {"username": "9876543210", "password": password})

    def test_owner_logs_in(self):
        response = self.login("right-password-1")
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)

    def test_lockout_after_five_wrong_passwords(self):
        for _ in range(4):
            response = self.login("wrong")
            self.assertContains(response, "The username or password is wrong")
        response = self.login("wrong")
        self.assertContains(response, "locked for 15 minutes")
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_locked())
        response = self.login("right-password-1")  # still locked
        self.assertContains(response, "locked for 15 minutes")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_success_resets_counter(self):
        self.login("wrong")
        self.login("wrong")
        self.login("right-password-1")
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_logins, 0)

    def test_first_login_forces_password_change(self):
        self.user.must_change_password = True
        self.user.save()
        self.client.force_login(self.user)
        response = self.client.get(reverse("employees:list"))
        self.assertRedirects(response, reverse("password_change"))
        response = self.client.post(reverse("password_change"), {
            "old_password": "right-password-1", "new_password1": "Kirana#2026store", "new_password2": "Kirana#2026store"})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)

    def test_inactive_company_cannot_log_in(self):
        Company.objects.filter(pk=self.company.pk).update(is_active=False)
        response = self.login("right-password-1")
        self.assertContains(response, "switched off")

    def test_superuser_sent_to_admin(self):
        admin = User.objects.create_superuser(username="platform", password="admin-pass-123")
        self.client.force_login(admin)
        response = self.client.get(reverse("employees:list"))
        self.assertRedirects(response, "/admin/", fetch_redirect_response=False)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("payroll:months"))
        self.assertRedirects(response, reverse("login") + "?next=" + reverse("payroll:months"))

    def test_forgot_password_page(self):
        response = self.client.get(reverse("password_forgot"))
        self.assertContains(response, "Please call or WhatsApp")

    @override_settings(SESSION_COOKIE_AGE=8 * 60 * 60)
    def test_session_length(self):
        from django.conf import settings

        self.assertEqual(settings.SESSION_COOKIE_AGE, 28800)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)


class AdminHomeTest(TestCase):
    def test_admin_home_says_what_to_use(self):
        admin_user = User.objects.create_superuser("platformadmin", password="a-long-test-password")
        self.client.force_login(admin_user)
        page = self.client.get("/admin/")
        self.assertContains(page, "What to use here")
        self.assertContains(page, "Create owner login")
        self.assertNotContains(page, "/admin/auth/group/")
