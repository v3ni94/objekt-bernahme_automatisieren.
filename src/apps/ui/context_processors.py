import os


def app_context(request):
    return {
        "APP_NAME": "Objektübernahme",
        "APP_OWNER": "Hausverwaltung Müller GmbH",
        "IMAGE_TAG": os.environ.get("IMAGE_TAG", "dev"),
    }
