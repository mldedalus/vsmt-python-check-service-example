import requests
import time
from threading import Lock

class FhirTokenService:
    def __init__(self):
        self.token = None
        self.expiry_time = 0
        self.lock = Lock()

    def get_token(self, token_endpoint, client_id, client_secret, force=False):
        """
        Get a valid token, refreshing it if necessary.
        """
        with self.lock:
            if self.token is None or time.time() >= self.expiry_time or force:
                self.refresh_token(token_endpoint, client_id, client_secret)
        return self.token

    def refresh_token(self, token_endpoint, client_id, client_secret):
        """
        Refresh the token by making a request to the token endpoint.
        """
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        data = {
            'grant_type': 'client_credentials',
            'client_id': client_id,
            'client_secret': client_secret
        }

        try:
            response = requests.post(token_endpoint, headers=headers, data=data)
            response.raise_for_status()  # Raise an exception for HTTP errors

            response_data = response.json()
            self.token = response_data.get("access_token")
            expires_in = response_data.get("expires_in", 0)

            if not self.token or not expires_in:
                raise RuntimeError("Invalid response: missing 'access_token' or 'expires_in'")

            # Set expiry time, refreshing 1 minute early
            self.expiry_time = time.time() + expires_in - 60

        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to refresh token: {e}")

# Example usage in Flask
from flask import Flask, jsonify

app = Flask(__name__)

# Create an instance of the token service
token_service = FhirTokenService()

@app.route('/get_fhir_token', methods=['GET'])
def get_fhir_token():
    try:
        # Replace with actual token endpoint, client ID, and secret
        token_endpoint = "https://example.com/token"
        client_id = "your-client-id"
        client_secret = "your-client-secret"

        token = token_service.get_token(token_endpoint, client_id, client_secret)
        return jsonify({"token": token}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
