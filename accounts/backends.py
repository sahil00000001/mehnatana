"""Username + password login with a lock after too many wrong passwords (README §2)."""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.utils import timezone

from .models import User


def find_user(username):
    username = (username or "").strip()
    if not username:
        return None
    return User.objects.filter(username=username).first() or User.objects.filter(username__iexact=username).first()


class LockoutBackend(ModelBackend):
    """Locks a username for LOGIN_LOCK_MINUTES after LOGIN_MAX_FAILURES wrong passwords.

    Used by the one login page (the admin screens send people there too).
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if not username or password is None:
            return None
        user = find_user(username)
        if user is None:
            User().set_password(password)  # same work as a real check, so timing gives nothing away
            return None
        now = timezone.now()
        if user.locked_until and user.locked_until > now:
            if request is not None:
                request.login_locked_until = user.locked_until
            return None
        if user.check_password(password):
            if not self.user_can_authenticate(user):
                return None
            if user.failed_logins or user.locked_until:
                User.objects.filter(pk=user.pk).update(failed_logins=0, locked_until=None)
                user.failed_logins, user.locked_until = 0, None
            return user
        failed = user.failed_logins + 1
        locked_until = None
        if failed >= settings.LOGIN_MAX_FAILURES:
            locked_until = now + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
            failed = 0
            if request is not None:
                request.login_locked_until = locked_until
        User.objects.filter(pk=user.pk).update(failed_logins=failed, locked_until=locked_until)
        return None
