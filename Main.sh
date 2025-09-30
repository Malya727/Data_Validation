#!/bin/sh

# Paths to your certificate and private key
CERT_FILE="/path/to/your/ca_certificate.pem"
PRIVATE_KEY_FILE="/path/to/your/private_key.pem"
encoded_data=$(base64 -w 0 /tmp/random_bytes.bin)
encoded_signed_data=$(echo "$signed_data" | base64 -w 0)

# Authentication URL
AUTH_URL="https://auth.anaplan.com/token/authenticate"

# Function to generate base64-encoded certificate
generate_encoded_cert() {
    if [[ -f "$CERT_FILE" ]]; then
        base64_cert=$(base64 -w 0 "$CERT_FILE")
        echo "$base64_cert"
    else
        echo "Error: Certificate file not found." >&2
        exit 1
    fi
}

# Function to generate encodedData and encodedSignedData
generate_encoded_strings() {
    random_bytes=$(openssl rand 100)
    echo -n "$random_bytes" > /tmp/random_bytes.bin
    signed_data=$(openssl dgst -sha512 -sign "$PRIVATE_KEY_FILE" /tmp/random_bytes.bin)
    encoded_data=$(base64 -w 0 /tmp/random_bytes.bin)
    encoded_signed_data=$(echo "$signed_data" | base64 -w 0)
    rm /tmp/random_bytes.bin
    echo "$encoded_data" "$encoded_signed_data"
}

# Generate certificate and encoded strings
echo "Generating base64-encoded CA certificate..."
CA_CERTIFICATE=$(generate_encoded_cert)

echo "Generating encodedData and encodedSignedData..."
read ENCODED_DATA ENCODED_SIGNED_DATA < <(generate_encoded_strings)

# Create JSON payload
payload=$(cat <<EOF
{
  "encodedData": "$ENCODED_DATA",
  "encodedSignedData": "$ENCODED_SIGNED_DATA"
}
EOF
)

# Make the POST request
response=$(curl -s -X POST "$AUTH_URL" \
-H "Authorization: $CA_CERTIFICATE" \
-H "Content-Type: application/json" \
-d "$payload")

# Extract the token value
auth_token=$(echo "$response" | grep -o '"tokenValue":"[^"]*' | cut -d'"' -f4)

# Output the token
echo "AnaplanAuthToken $auth_token"
