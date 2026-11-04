from .audit import audit


def log_login(sender, request, user, **kwargs):
    audit(request, "login", obj=user, company=getattr(user, "company", None))
