from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User


class LoginTest(TestCase):
    """One login opens every company; there is no separate admin or business login."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(username="manager", password="right-password-1")

    def login(self, password):
        return self.client.post(reverse("login"), {"username": "manager", "password": password})

    def test_login_lands_on_all_companies(self):
        response = self.login("right-password-1")
        self.assertRedirects(response, reverse("portal:list"), fetch_redirect_response=False)

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

    def test_temporary_password_must_be_changed(self):
        self.user.must_change_password = True
        self.user.save()
        self.client.force_login(self.user)
        response = self.client.get(reverse("portal:list"))
        self.assertRedirects(response, reverse("password_change"))
        response = self.client.post(reverse("password_change"), {
            "old_password": "right-password-1", "new_password1": "Kirana#2026store", "new_password2": "Kirana#2026store"})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)

    def test_switched_off_login_cannot_log_in(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        response = self.login("right-password-1")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(response.status_code, 200)

    def test_anonymous_redirected_to_login(self):
        for name in ("payroll:months", "portal:list"):
            response = self.client.get(reverse(name))
            self.assertRedirects(response, reverse("login") + "?next=" + reverse(name))

    def test_forgot_password_page(self):
        response = self.client.get(reverse("password_forgot"))
        self.assertContains(response, "Please call or WhatsApp")
        self.assertNotContains(response, "administrator")

    def test_login_page_has_one_username_box(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "Username")
        self.assertNotContains(response, "mobile number")

    @override_settings(SESSION_COOKIE_AGE=8 * 60 * 60)
    def test_session_length(self):
        from django.conf import settings

        self.assertEqual(settings.SESSION_COOKIE_AGE, 28800)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)


class AdminScreensTest(TestCase):
    """The admin screens keep only the rate tables, behind the same one login."""

    def test_admin_login_goes_to_the_one_login_page(self):
        response = self.client.get("/admin/login/?next=/admin/")
        self.assertRedirects(response, reverse("login") + "?next=%2Fadmin%2F", fetch_redirect_response=False)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_admin_home_has_only_rate_tables(self):
        user = User.objects.create_superuser("manager", password="a-long-test-password")
        self.client.force_login(user)
        page = self.client.get("/admin/")
        self.assertContains(page, "What to use here")
        self.assertContains(page, "Statutory tables")
        self.assertNotContains(page, "Create owner login")
        self.assertNotContains(page, "/admin/companies/company/")
        self.assertNotContains(page, "/admin/accounts/user/")
        self.assertNotContains(page, "/admin/auth/group/")
        self.assertEqual(self.client.get("/admin/companies/company/").status_code, 404)
        self.assertEqual(self.client.get("/admin/accounts/user/").status_code, 404)

    def test_login_without_staff_rights_is_not_looped(self):
        user = User.objects.create_user("helper", password="a-long-test-password")
        self.client.force_login(user)
        response = self.client.get("/admin/login/?next=/admin/")
        self.assertRedirects(response, reverse("portal:list"), fetch_redirect_response=False)
