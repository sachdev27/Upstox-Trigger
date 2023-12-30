import jwt,logging
from jwt import api_jwt
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def _check(token):
    if token == "":return True
    try:
        decoded_token = api_jwt.decode(jwt=token,algorithms=['HS256'],verify=False)
        exp_timestamp = decoded_token['exp']
        exp_datetime = datetime.utcfromtimestamp(exp_timestamp).replace(tzinfo=timezone.utc)

        current_datetime = datetime.utcnow().replace(tzinfo=timezone.utc)

        return exp_datetime < current_datetime

    except jwt.ExpiredSignatureError:
        # If the token is explicitly expired, consider it expired
        return True
    except jwt.InvalidTokenError:
        # If there's any issue with the token, consider it expired
        return True


def token_expired(token):
    if _check(token):
        logger.info("Token is expired.")
        return True
    else:
        logger.info("Token is still valid.")
        return False
