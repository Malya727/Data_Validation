import os
import json
import time
import requests
from base64 import b64encode
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA512
from Crypto.Random import get_random_bytes


# === CONFIGURATION (define all paths and secrets here) ===
CERT_FILE = "/absolute/path/to/your/cert.pem"
PRIVATE_KEY_FILE = "/absolute/path/to/your/private_key.pem"
PRIVATE_KEY_PASSPHRASE = "yourPrivateKeyPassphrase"  # Leave as "" if not encrypted

FLOW_ID = "ac72a95201394ced8328c9a46245a9d7"
PAYLOAD_VERSION = "v2"  # Options: "v1", "v2"
AUTH_URL = "https://auth.anaplan.com/token/authenticate"
CLOUDWORKS_URL = f"https://api.cloudworks.anaplan.com/2/0/integrations/{FLOW_ID}/run"


# === FUNCTION: Generate Anaplan authentication payload ===
def generate_payload(private_key_path, passphrase=None, payload_version="v2"):
    if payload_version not in ["v1", "v2"]:
        raise ValueError("Invalid payload version. Must be 'v1' or 'v2'.")

    # Generate message bytes
    if payload_version == "v2":
        epoch_seconds = int(time.time())
        message_bytes = epoch_seconds.to_bytes(8, 'big') + get_random_bytes(92)
    else:
        message_bytes = get_random_bytes(100)

    # Base64 encode message
    encoded_data = b64encode(message_bytes).decode("utf-8")

    # Load private key and sign message
    with open(private_key_path, "r", encoding="utf-8") as key_file:
        key_content = key_file.read()
        private_key = RSA.import_key(key_content, passphrase=passphrase)

    signer = pkcs1_15.new(private_key)
    message_hash = SHA512.new(message_bytes)
    signature = signer.sign(message_hash)
    encoded_signed_data = b64encode(signature).decode("utf-8")

    # Final payload
    payload = {}
    if payload_version == "v2":
        payload["encodedDataFormat"] = "v2"
    payload["encodedData"] = encoded_data
    payload["encodedSignedData"] = encoded_signed_data

    return json.dumps(payload)


# === FUNCTION: Read and encode certificate ===
def load_certificate_base64(cert_path):
    if not os.path.isfile(cert_path):
        raise FileNotFoundError(f"Certificate file not found: {cert_path}")
    with open(cert_path, "rb") as cert_file:
        return b64encode(cert_file.read()).decode("utf-8")


# === FUNCTION: Authenticate and get token ===
def get_auth_token(payload, certificate_base64):
    headers = {
        "Authorization": certificate_base64,
        "Content-Type": "application/json"
    }

    print("🔐 Requesting authentication token...")
    response = requests.post(AUTH_URL, headers=headers, data=payload)
    if response.status_code != 200:
        print("❌ Authentication failed:")
        print(response.text)
        raise SystemExit(1)

    try:
        token_value = response.json()["tokenInfo"]["tokenValue"]
        print("✅ Auth token acquired.")
        return token_value
    except Exception:
        print("❌ Failed to parse token from response:")
        print(response.text)
        raise SystemExit(1)


# === FUNCTION: Trigger CloudWorks integration ===
def trigger_cloudworks(auth_token):
    print("🚀 Triggering CloudWorks integration...")
    headers = {
        "Authorization": f"AnaplanAuthToken {auth_token}",
        "Content-Type": "application/json"
    }

    response = requests.post(CLOUDWORKS_URL, headers=headers)
    print("✅ CloudWorks response:")
    print(response.text)


# === MAIN SCRIPT EXECUTION ===
def main():
    try:
        print("📦 Generating signed payload...")
        payload = generate_payload(
            private_key_path=PRIVATE_KEY_FILE,
            passphrase=PRIVATE_KEY_PASSPHRASE or None,
            payload_version=PAYLOAD_VERSION
        )
        print("✅ Payload generated.")

        print("📄 Encoding certificate...")
        cert_base64 = load_certificate_base64(CERT_FILE)

        auth_token = get_auth_token(payload, cert_base64)
        trigger_cloudworks(auth_token)

    except Exception as e:
        print(f"❌ Error: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
