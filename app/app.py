import os, mimetypes, io, json, logging, yaml, requests
from flask import Flask, Response, request, jsonify
from botocore.exceptions import NoCredentialsError, ClientError
from flask import Flask, render_template, jsonify, send_file, abort
from werkzeug.utils import secure_filename
from flask_oidc import OpenIDConnect
from werkzeug.middleware.proxy_fix import ProxyFix

# Path to the configuration file
CONFIG_FILE_PATH = os.getenv('CONFIG_FILE_PATH', "/opt/application.yml")

# Function to load configuration from a YAML file
def load_config(file_path):
    try:
        with open(file_path, "r") as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        raise RuntimeError(f"Configuration file not found at {file_path}")
    except yaml.YAMLError as e:
        raise RuntimeError(f"Error parsing YAML configuration: {e}")

app = Flask(__name__)

CONTEXT_PATH = os.getenv('CONTEXT_PATH')

PROXIED = os.getenv('PROXIED')

if PROXIED == 'true':
    print("Using ProxyFix")
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )

# Load the configuration on startup
app.config["APP_CONFIG"] = load_config(CONFIG_FILE_PATH)

class FhirServerConfig:
    def __init__(self, endpoint, auth_endpoint, client_id, client_secret):
        self.endpoint = endpoint
        self.auth_endpoint = auth_endpoint
        self.client_id = client_id
        self.client_secret = client_secret

class TerminologyServerConfig:
    def __init__(self, endpoint, auth_endpoint, client_id, client_secret):
        self.endpoint = endpoint
        self.auth_endpoint = auth_endpoint
        self.client_id = client_id
        self.client_secret = client_secret

# Example FHIR server configuration
fhir_server_config = FhirServerConfig(
    endpoint=app.config["APP_CONFIG"]["checks"]["fhirServer"]["endpoint"],
    auth_endpoint=app.config["APP_CONFIG"]["checks"]["fhirServer"]["authenticationEndpoint"],
    client_id=app.config["APP_CONFIG"]["checks"]["fhirServer"]["client_id"],
    client_secret=app.config["APP_CONFIG"]["checks"]["fhirServer"]["client_secret"]
)

terminology_server_config = TerminologyServerConfig(
    endpoint=app.config["APP_CONFIG"]["checks"]["terminologyServer"]["endpoint"],
    auth_endpoint=app.config["APP_CONFIG"]["checks"]["terminologyServer"]["authenticationEndpoint"],
    client_id=app.config["APP_CONFIG"]["checks"]["terminologyServer"]["client_id"],
    client_secret=app.config["APP_CONFIG"]["checks"]["terminologyServer"]["client_secret"]
)

# This is config that would be used to apply OIDC AuthZ/AuthN from client_secrets.json
#app.config.update({
#    'SECRET_KEY': os.getenv('OIDC_SECRET_KEY'),
#    'OIDC_CLIENT_SECRETS': 
#    { 
#        "web": {
#            "client_id": os.getenv('OIDC_CLIENT_ID'), 
#            "client_secret": os.getenv('OIDC_CLIENT_SECRET'), 
#            "auth_uri": f"{os.getenv('OIDC_ISSUER')}/protocol/openid-connect/auth",
#            "token_uri": f"{os.getenv('OIDC_ISSUER')}/protocol/openid-connect/token",
#            "userinfo_uri": f"{os.getenv('OIDC_ISSUER')}/protocol/openid-connect/userinfo",
#            "issuer": os.getenv('OIDC_ISSUER') 
#        }
#    },
#    'OIDC_SCOPES': ['openid', 'email', 'profile'],
#    'OIDC_INTROSPECTION_AUTH_METHOD': 'client_secret_post',
#    'OIDC_COOKIE_SECURE': False,  # Set to True in production (to use HTTPS)
#    'OIDC_USER_INFO_ENABLED': True,
#    'OIDC_SERVER_METADATA_URL': os.getenv('OIDC_SERVER_METADATA_URL')  #'https://terminologystandardsservice.ca/authorisation/auth/realms/master/.well-known/openid-configuration',
#})

# Uncomment if using OIDC
#content_or_filepath = app.config.get("OIDC_CLIENT_SECRETS", None)
#if content_or_filepath is not None and isinstance(content_or_filepath, dict):
#    print("Loading OIDC configuration from dict")
#    print("Output of content_or_filepath.values()")
#    print(content_or_filepath.values())
#    print("list(content_or_filepath.values())[0]")
#    print(list(content_or_filepath.values())[0])
 
# Uncomment if using OIDC
#oidc = OpenIDConnect(app)


### Helper Functions

# This function extracts the ActivityDefinition, and the Focus from the Payload
# Returns a dict containing "valid" (bool) and "focusResource" (dict), "activityDefition" (dict), and "operationOutcome" (dict)
def validate_task_request(fhir_payload):
    """Validate a Task request and return a dict containing "valid" (bool) and "focusResource" (dict), "activityDefition" (dict), and "operationOutcome" (dict)"""

    response_dict = {
        "valid": True,
        "focusResource": None,
        "activityDefinition": None,
        "operationOutcome": None
    }


    # Ensure the paload is a Task resource
    if fhir_payload.get("resourceType") != "Task":

        operation_outcome = {
            "resourceType": "OperationOutcome",
            "issue": [
                {
                    "severity": "error",
                    "code": "invalid",
                    "diagnostics": f"Failed to parse Task resource: expected 'Task' resourceType but got '{fhir_payload.get('resourceType')}'"
                }
            ]
        }

        response_dict["valid"] = False
        response_dict["operationOutcome"] = operation_outcome
        return response_dict

    
    # Check if Task.focus is referencing a ValueSet
    focus_reference = fhir_payload.get("focus", {}).get("reference", None)
    logging.info(f"Task focus reference is: {focus_reference}. Trying to resolve.")
    focus_resource = resolve_focus_reference(fhir_payload, focus_reference)
    if focus_reference is None:
        operation_outcome = {
            "resourceType": "OperationOutcome",
            "issue": [
                {
                    "severity": "error",
                    "code": "invalid",
                    "diagnostics": f"Task focus is missing or null"
                }
            ]
        }

        response_dict["valid"] = False
        response_dict["operationOutcome"] = operation_outcome
        return response_dict
    elif focus_resource is not None and focus_resource.get("resourceType") != "ValueSet":
        operation_outcome = {
            "resourceType": "OperationOutcome",
            "issue": [
                {
                    "severity": "error",
                    "code": "invalid",
                    "diagnostics": f"Task focus is not a ValueSet, but it should be. Got '{focus_resource.get('resourceType')}'"
                }
            ]
        }

        response_dict["valid"] = False
        response_dict["operationOutcome"] = operation_outcome
        return response_dict
    
    logging.info(f"Task focus resolved successfully to ValueSet: {focus_resource.get('url')}")
    response_dict["focusResource"] = focus_resource


    # Check if Task.instantiatesCanonical is referencing an ActivityDefinition
    instantiates_canonical = fhir_payload.get("instantiatesCanonical", None)
    activity_definition = resolve_activity_definition(instantiates_canonical)
    if activity_definition.get("resourceType") == "ActivityDefinition":
        response_dict["activityDefinition"] = activity_definition
    elif activity_definition.get("resourceType") == "OperationOutcome":
        response_dict["valid"] = False
        response_dict["operationOutcome"] = activity_definition
        return response_dict
    else:
        operation_outcome = {
            "resourceType": "OperationOutcome",
            "issue": [
                {
                    "severity": "error",
                    "code": "invalid",
                    "diagnostics": f"Task instantiatesCanonical is not an ActivityDefinition, or an OperationOutcome, but it should be. Got '{activity_definition}'"
                }
            ]
        }
        logging.error(f"Task instantiatesCanonical is not an ActivityDefinition, or an OperationOutcome, but it should be. {json.dumps(activity_definition)}")
        response_dict["valid"] = False
        response_dict["operationOutcome"] = operation_outcome
        return response_dict
    
    response_dict["valid"] = True
    return response_dict

def resolve_focus_reference(fhir_payload, focus_reference):
    if focus_reference and focus_reference.startswith("#"):
        contained_id = focus_reference[1:]  # Remove the '#' prefix
        value_set = next(
            (res for res in fhir_payload.get("contained", []) if res.get("id") == contained_id), 
            None
        )
        if value_set:
            #print("ValueSet resource content:")
            #print(json.dumps(value_set, indent=4))
            return value_set
        else:
            return None
    elif focus_reference.contains("/"):
        # Fetch the ValueSet from the FHIR server
        return resolve_external_reference(focus_reference.split("/")[0], focus_reference.split("/")[1], fhir_server_config)
    else:
        logging.error("Invalid or missing focus reference.")
        return None

def resolve_external_reference(resource, resource_id, fhir_server):
    """Retrieve a resource by type and ID from a FHIR server."""
    # Fetch the OAuth2 token
    token = fetch_token(fhir_server.auth_endpoint, fhir_server.client_id, fhir_server.client_secret)

    # Construct the FHIR server URL for the resource
    resource_url = f"{fhir_server.endpoint}/{resource}/{resource_id}"

    # HTTP headers for the FHIR request
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/fhir+json",
        "Content-Type": "application/fhir+json",
    }

    # Perform the GET request to retrieve the resource
    try:
        response = requests.get(resource_url, headers=headers)
        response.raise_for_status()
        return response.json()  # Return the resource as a JSON object
    except requests.HTTPError as e:
        error_message = f"Error fetching resource type {resource} with id '{resource_id}': {e.response.text}"
        raise RuntimeError(error_message)
    except requests.RequestException as e:
        error_message = f"Error connecting to the FHIR server: {e}"
        raise RuntimeError(error_message)

def fetch_token(auth_endpoint, client_id, client_secret):
    """Fetch an OAuth2 token."""
    try:
        response = requests.post(
            auth_endpoint,
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
        )
        if response.status_code == 401 and "error" in response.json() and "unauthorized_client" in response.json()["error"]:
            logging.error(f"Invalid credentials fetching token at {auth_endpoint} using client_id {client_id} with secret {client_secret}")
        response.raise_for_status()
        return response.json().get("access_token")
    except requests.RequestException as e:
        raise RuntimeError(f"Error fetching token: {e}")
    
def resolve_activity_definition(instantiates_canonical_url):

    """Retrieve an ActivityDefinition resource by its canonical URL."""
    # Fetch the token
    token = fetch_token(fhir_server_config.auth_endpoint, fhir_server_config.client_id, fhir_server_config.client_secret)

    # Build the search URL
    search_url = f"{fhir_server_config.endpoint}/ActivityDefinition"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/fhir+json"}
    params = {"url": instantiates_canonical_url}

    # Search for the ActivityDefinition
    try:
        response = requests.get(search_url, headers=headers, params=params)
        response.raise_for_status()

        bundle = response.json()

        # Check the number of entries in the Bundle
        total = bundle.get("total", 0)
        if total == 0:
            operation_outcome = {
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "invalid",
                        "diagnostics": f"ActivityDefinition not found for canonical URL '{instantiates_canonical_url}'"
                    }
                ]
            }
            return operation_outcome
        elif total > 1:
            operation_outcome = {
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "invalid",
                        "diagnostics": f"Multiple ActivityDefinitions found for canonical URL '{instantiates_canonical_url}'"
                    }
                ]
            }
            return operation_outcome
        else:
            # Return the first resource in the Bundle
            resource = bundle["entry"][0]["resource"]
            return resource

    except requests.RequestException as e:
        raise RuntimeError(f"Error resolving ActivityDefinition: {e}")


### Main Service Functions

@app.route(f"/api/health")
def home():
    return jsonify({'health': 'Ok!'}), 200

@app.route(f'/api/check', methods=['POST'])
#@oidc.require_login
def process_task():

    # Parse the JSON payload and validate it's all ok
    payload = request.get_json()

    # Validate the Task request 
    validation_result = validate_task_request(payload)

    if not validation_result.get("valid"):
        return jsonify(validation_result.get("operationOutcome")), 400
    
    activity_definition = validation_result.get("activityDefinition")
    focus_resource = validation_result.get("focusResource")

    check_name = activity_definition.get("code", {}).get("coding", [{}])[0].get("code", None)
    logging.info(f"Processing check with name {check_name}")

    issues = []

    ### Check code goes here
    if check_name == "code-format":
        valueset = focus_resource
        # Example check logic here is a simple format check
        # Check if 'expansion' and 'contains' exist in the ValueSet
        if 'expansion' in valueset and 'contains' in valueset['expansion']:
            concepts = valueset['expansion']['contains']
            
            for concept in concepts:
                # Check if the concept code is present and is a number
                if 'code' in concept and concept['system'] == 'http://snomed.info/sct':
                    try:
                        float(concept['code'])  # Try converting the code to a number
                    except ValueError:
                        # If conversion fails, add an issue to the OperationOutcome
                        issues.append({
                            "severity": "error",
                            "code": "invalid-format",
                            "details": {
                                "coding": [
                                    {
                                        "system": "http://vsmt.dedalus.eu/issue-detail",
                                        "code": "INVALID_CONCEPT_IDENTIFIER_FORMAT",
                                        "display": "Concept is not in the correct format for the CodeSystem"
                                    }
                                ],
                                "text": "The provided identifier is not a valid SNOMED CT Concept ID."
                            },
                            "diagnostics": f"Identifier {concept['code']} is an invalid format",
                            "expression": [
                                f"ValueSet.expansion.contains.where(system = '{concept['system']}' ).concept.where(code = '{concept['code']}')"
                            ]
                        })
        operation_outcome = {}
        operation_outcome["resourceType"] = "OperationOutcome"
        operation_outcome["issue"] = issues
        response = json.dumps(operation_outcome, sort_keys=False)

        return Response(response, content_type="application/json")
    
    else:   
        return jsonify({'error': f'Unsupported check in ActivityDefinition: {check_name}'}), 400
        
if __name__ == '__main__':
    app.run(debug=False,port=8085)
    