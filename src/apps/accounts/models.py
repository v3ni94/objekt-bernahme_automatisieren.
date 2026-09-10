"""Nutzer und Rollen (Fachentwurf D 10.1, Tabellen roles und users).

Abweichung zu D (dokumentiert fuer das Bezeichnerregister in M2): Die TOTP-Geheimnisse und
Wiederherstellungscodes liegen nicht in users.totp_*, sondern in der Tabelle des allauth-MFA-Moduls
(mfa_authenticator), verschluesselt mit TOTP_KEY ueber den MFA-Adapter (apps.accounts.adapters).
"""

from __future__ import annotations

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models import Case, F, Value, When
from django.utils import timezone


class Role(models.Model):
    ADMIN = "admin"
    CLERK = "sachbearbeiter"

    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=80)
    permissions = models.JSONField(default=list, help_text="Liste von Berechtigungsschluesseln")
    is_system = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "roles"
        ordering = ["code"]

    def __str__(self) -> str:
        return self.name

    def has_perm(self, code: str) -> bool:
        return code in (self.permissions or [])


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, username):
        return self.get(
            **{self.model.USERNAME_FIELD: self.normalize_email(username).lower(), "deleted_at__isnull": True}
        )

    def _create(self, email: str, password: str | None, role: Role, **extra):
        if not email:
            raise ValueError("E-Mail ist Pflicht")
        email = self.normalize_email(email).lower()
        extra.setdefault("display_name", email.split("@")[0])
        extra.setdefault("status", User.Status.ACTIVE)
        user = self.model(email=email, role=role, **extra)
        if password:
            user.set_password(password)
            user.password_changed_at = timezone.now()
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, role: Role | None = None, **extra):
        role = role or Role.objects.get(code=Role.CLERK)
        return self._create(email, password, role, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        role = Role.objects.get(code=Role.ADMIN)
        return self._create(email, password, role, **extra)


class User(AbstractBaseUser):
    class Status(models.TextChoices):
        INVITED = "invited", "eingeladen"
        ACTIVE = "active", "aktiv"
        DISABLED = "disabled", "gesperrt"

    email = models.EmailField(max_length=254)
    display_name = models.CharField(max_length=120)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, db_column="role_id", related_name="users")
    password = models.CharField(max_length=255, null=True, blank=True, db_column="password_hash")
    password_changed_at = models.DateTimeField(null=True, blank=True)
    google_subject = models.CharField(max_length=255, null=True, blank=True, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.INVITED)
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_login = models.DateTimeField(null=True, blank=True, db_column="last_login_at")
    created_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, db_column="created_by", related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, db_column="deleted_by", related_name="+"
    )
    delete_reason = models.CharField(max_length=255, null=True, blank=True)
    # Eindeutigkeit der E-Mail nur fuer nicht geloeschte Zeilen (D 10.1)
    active_key = models.GeneratedField(
        expression=Case(When(deleted_at__isnull=True, then=F("email")), default=Value(None)),
        output_field=models.EmailField(max_length=254, null=True),
        db_persist=True,
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["display_name"]

    class Meta:
        db_table = "users"
        constraints = [models.UniqueConstraint(fields=["active_key"], name="uq_users_email_active")]
        indexes = [models.Index(fields=["role"], name="ix_users_role")]

    def __str__(self) -> str:
        return self.email

    # --- Zustand ---------------------------------------------------------------------------
    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE and self.deleted_at is None

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())

    @property
    def is_admin(self) -> bool:
        return self.role_id is not None and self.role.code == Role.ADMIN

    # Django-Admin wird nicht genutzt; die Attribute existieren fuer Bibliotheken, die sie erwarten.
    @property
    def is_staff(self) -> bool:
        return self.is_admin

    @property
    def is_superuser(self) -> bool:
        return False

    # --- Rechte (docs/architektur.md 9.1) --------------------------------------------------------
    def has_permission(self, code: str) -> bool:
        return self.is_active and self.role.has_perm(code)

    def get_full_name(self) -> str:
        return self.display_name

    def get_short_name(self) -> str:
        return self.display_name
