import os
import json
import time
import requests
from base64 import b64encode
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA512
from Crypto.Random import get_random_bytes
from datetime import datetime


# === CONFIGURATION ===
CERT_FILE = "/absolute/path/to/your/cert.pem"                # Path to public certificate
PRIVATE_KEY_FILE = "/absolute/path/to/your/private_key.pem"  # Path to private key (PEM)
PRIVATE_KEY_PASSPHRASE = "yourPrivateKeyPassphrase"          # Set to "" if not encrypted
LOG_FILE_PATH = "/your/onprem/path/anaplan_cloudworks_log.txt"  # Log file path

FLOW_ID = "ac72a95201394ced8328c9a46245a9d7"
PAYLOAD_VERSION = "v2"  # Must be "v2" to comply with current Anaplan standard
AUTH_URL = "https://auth.anaplan.com/token/authenticate"
CLOUDWORKS_URL = f"https://api.cloudworks.anaplan.com/2/0/integrations/{FLOW_ID}/run"


# === LOGGING FUNCTION ===
def log_message(message, divider=False, print_console=False):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{timestamp}] {message}"
    with open(LOG_FILE_PATH, "a", encoding="utf-8") as log_file:
        if divider:
            log_file.write("\n" + "=" * 60 + "\n")
        log_file.write(full_message + "\n")
    if print_console:
        print(full_message)


# === GENERATE PAYLOAD ===
def generate_payload(private_key_path, passphrase=None, payload_version="v2"):
    if payload_version != "v2":
        raise ValueError("Only 'v2' payload is supported as per Anaplan guidance.")

    # Construct payload as [epoch (8 bytes) + random (92 bytes)]
    epoch_seconds = int(time.time())
    message_bytes = epoch_seconds.to_bytes(8, 'big') + get_random_bytes(92)
    encoded_data = b64encode(message_bytes).decode("utf-8")

    # Load private key
    with open(private_key_path, "r", encoding="utf-8") as key_file:
        key_content = key_file.read()
        private_key = RSA.import_key(key_content, passphrase=passphrase)

    # Sign payload using SHA512
    signer = pkcs1_15.new(private_key)
    message_hash = SHA512.new(message_bytes)
    signature = signer.sign(message_hash)
    encoded_signed_data = b64encode(signature).decode("utf-8")

    return json.dumps({
        "encodedDataFormat": "v2",
        "encodedData": encoded_data,
        "encodedSignedData": encoded_signed_data
    })


# === LOAD BASE64 CERTIFICATE ===
def load_certificate_base64(cert_path):
    if not os.path.isfile(cert_path):
        raise FileNotFoundError(f"Certificate file not found: {cert_path}")
    with open(cert_path, "rb") as cert_file:
        return b64encode(cert_file.read()).decode("utf-8")


# === GET AUTH TOKEN ===
def get_auth_token(payload, certificate_base64):
    headers = {
        "Authorization": certificate_base64,
        "Content-Type": "application/json"
    }
    response = requests.post(AUTH_URL, headers=headers, data=payload)
    if response.status_code != 200:
        log_message("Authentication failed.")
        log_message(f"HTTP {response.status_code}: {response.text}")
        raise SystemExit(1)

    try:
        token_value = response.json()["tokenInfo"]["tokenValue"]
        log_message("Authentication token acquired.")
        return token_value
    except Exception as e:
        log_message(f"Failed to parse token: {e}")
        log_message(response.text)
        raise SystemExit(1)


# === TRIGGER CLOUDWORKS FLOW ===
def trigger_cloudworks(auth_token):
    headers = {
        "Authorization": f"AnaplanAuthToken {auth_token}",
        "Content-Type": "application/json"
    }
    response = requests.post(CLOUDWORKS_URL, headers=headers)
    log_message("CloudWorks response:")
    log_message(f"HTTP {response.status_code}: {response.text}")
    if response.status_code != 202:
        log_message("Unexpected response when triggering integration.")
        raise SystemExit(1)
    log_message("CloudWorks integration triggered successfully.")


# === MAIN ===
def main():
    try:
        log_message("Starting Anaplan CloudWorks integration run...", divider=True)

        payload = generate_payload(
            private_key_path=PRIVATE_KEY_FILE,
            passphrase=PRIVATE_KEY_PASSPHRASE or None,
            payload_version=PAYLOAD_VERSION
        )
        log_message("Payload generated successfully.")

        cert_base64 = load_certificate_base64(CERT_FILE)
        log_message("Certificate loaded and encoded successfully.")

        auth_token = get_auth_token(payload, cert_base64)
        trigger_cloudworks(auth_token)

    except Exception as e:
        log_message(f"Error occurred: {e}", print_console=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
