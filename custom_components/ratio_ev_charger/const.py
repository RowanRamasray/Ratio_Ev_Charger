DOMAIN = "ratio_ev_charger"

DEFAULT_REGION = "eu-west-1"
DEFAULT_CLIENT_ID = "78cs05mc0hc5ibqv1tui22n962"
DEFAULT_USER_POOL_ID = "eu-west-1_mH4sFjLoF"
DEFAULT_IDENTITY_POOL_ID = "eu-west-1:893982c4-6d19-4180-b7d2-468a03595496"

COGNITO_ISSUER = "cognito-idp.{region}.amazonaws.com/{user_pool_id}"

API_SERVICE = "execute-api"

# Service names
SERVICE_START_CHARGE = "start_charge"
SERVICE_STOP_CHARGE = "stop_charge"
