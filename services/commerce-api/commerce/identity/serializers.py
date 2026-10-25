from typing import Any

from rest_framework import serializers

from .models import ExternalIdentity, IdentityProvider, OtpChallenge, OtpChannel, User
from .otp import CODE_LIFETIME, seconds_until_resend

PASSWORD_MAX_LENGTH = 128


def mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}{'•' * max(2, min(len(local) - 1, 6))}@{domain}"


def mask_phone(number: str) -> str:
    return f"{number[:4]} •••• {number[-3:]}" if len(number) > 7 else "••••"


def masked_destination(challenge: OtpChallenge) -> str:
    if challenge.channel == OtpChannel.SMS:
        return mask_phone(challenge.destination)
    return mask_email(challenge.destination)


class PasswordField(serializers.CharField):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", PASSWORD_MAX_LENGTH)
        kwargs.setdefault("trim_whitespace", False)
        kwargs.setdefault("style", {"input_type": "password"})
        kwargs.setdefault("write_only", True)
        super().__init__(**kwargs)


class CodeField(serializers.CharField):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", 16)
        super().__init__(**kwargs)


# --- Requests -------------------------------------------------------------------------------------


class EmptySerializer(serializers.Serializer[dict[str, Any]]):
    pass


class RegisterSerializer(serializers.Serializer[dict[str, Any]]):
    full_name = serializers.CharField(max_length=150)
    email = serializers.EmailField(max_length=254)
    password = PasswordField()


class ChallengeCodeSerializer(serializers.Serializer[dict[str, Any]]):
    challenge_id = serializers.UUIDField()
    code = CodeField()


class ChallengeRefSerializer(serializers.Serializer[dict[str, Any]]):
    challenge_id = serializers.UUIDField()


class LoginSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField(max_length=254)
    password = PasswordField(max_length=PASSWORD_MAX_LENGTH)


class EmailSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField(max_length=254)


class PhoneSerializer(serializers.Serializer[dict[str, Any]]):
    phone = serializers.CharField(max_length=32)


class PasswordResetSerializer(serializers.Serializer[dict[str, Any]]):
    token = serializers.CharField(max_length=128)
    password = PasswordField()


class PasswordChangeSerializer(serializers.Serializer[dict[str, Any]]):
    current_password = PasswordField(required=False, allow_blank=True, default="")
    new_password = PasswordField()


class PhoneCompleteSerializer(serializers.Serializer[dict[str, Any]]):
    challenge_id = serializers.UUIDField()
    completion_token = serializers.CharField(max_length=128)
    full_name = serializers.CharField(max_length=150)


class ReauthenticateSerializer(serializers.Serializer[dict[str, Any]]):
    password = PasswordField(required=False, allow_blank=True, default="")
    challenge_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    code = CodeField(required=False, allow_blank=True, default="")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs["password"] and not (attrs["challenge_id"] and attrs["code"]):
            raise serializers.ValidationError("Enter your password or the code we sent you.")
        return attrs


class ProfileUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    full_name = serializers.CharField(max_length=150)


# --- Responses ------------------------------------------------------------------------------------


class ChallengeSerializer(serializers.Serializer[OtpChallenge]):
    challenge_id = serializers.UUIDField(source="pk")
    channel = serializers.ChoiceField(choices=OtpChannel.choices)
    destination = serializers.SerializerMethodField()
    resend_in = serializers.SerializerMethodField(help_text="Seconds until a new code can be sent.")
    expires_in = serializers.SerializerMethodField(help_text="Seconds the code stays valid.")

    def get_destination(self, obj: OtpChallenge) -> str:
        return masked_destination(obj)

    def get_resend_in(self, obj: OtpChallenge) -> int:
        return seconds_until_resend(obj)

    def get_expires_in(self, obj: OtpChallenge) -> int:
        return int(CODE_LIFETIME.total_seconds())


class SignInMethodsSerializer(serializers.Serializer[dict[str, Any]]):
    email_password = serializers.BooleanField()
    phone = serializers.BooleanField()
    google = serializers.BooleanField()
    phone_country = serializers.CharField(
        help_text="ISO 3166-1 alpha-2 of the store, for national numbers."
    )


class CustomerAccountSerializer(serializers.Serializer[User]):
    id = serializers.UUIDField()
    full_name = serializers.CharField()
    email = serializers.EmailField(allow_null=True)
    email_verified = serializers.SerializerMethodField()
    phone = serializers.CharField(allow_null=True)
    phone_verified = serializers.SerializerMethodField()
    has_password = serializers.BooleanField()
    google_linked = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_email_verified(self, obj: User) -> bool:
        return obj.email_verified_at is not None

    def get_phone_verified(self, obj: User) -> bool:
        return obj.phone_verified_at is not None

    def get_google_linked(self, obj: User) -> bool:
        return ExternalIdentity.objects.filter(user=obj, provider=IdentityProvider.GOOGLE).exists()


class PhoneVerifyResultSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(choices=["signed_in", "needs_name"])
    completion_token = serializers.CharField(required=False)
    account = CustomerAccountSerializer(required=False)


class GoogleStartSerializer(serializers.Serializer[dict[str, Any]]):
    url = serializers.URLField()


class CsrfSerializer(serializers.Serializer[dict[str, Any]]):
    csrf_token = serializers.CharField()
