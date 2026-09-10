from django import forms
from django.contrib.auth.password_validation import validate_password

from .models import Role, User


class UserCreateForm(forms.Form):
    email = forms.EmailField(label="E-Mail", max_length=254)
    display_name = forms.CharField(label="Anzeigename", max_length=120)
    role = forms.ModelChoiceField(label="Rolle", queryset=Role.objects.all())
    password = forms.CharField(label="Startpasswort", widget=forms.PasswordInput, strip=False)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email, deleted_at__isnull=True).exists():
            raise forms.ValidationError("Diese E-Mail ist bereits vergeben.")
        return email

    def clean_password(self):
        pw = self.cleaned_data["password"]
        validate_password(pw)
        return pw


class UserRoleForm(forms.Form):
    role = forms.ModelChoiceField(label="Rolle", queryset=Role.objects.all())
    reason = forms.CharField(label="Begründung", max_length=255, required=False)
