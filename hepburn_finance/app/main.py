"""Flask application entry point. Started by /etc/services.d/finance/run via Docker."""
import os
import logging
from flask import Flask
from app.database import init_db
from app.routes import bp


class IngressMiddleware:
    """WSGI middleware to make Flask aware of the HA Ingress path prefix.

    HA Ingress sends an `X-Ingress-Path` header on every request — that's the
    base path the user hits (e.g. /api/hassio_ingress/abc123). For url_for()
    to generate working links, we need to set SCRIPT_NAME so Flask thinks
    the app is mounted at that prefix.
    """
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        ingress = environ.get('HTTP_X_INGRESS_PATH', '')
        if ingress:
            environ['SCRIPT_NAME'] = ingress
            path_info = environ.get('PATH_INFO', '')
            if path_info.startswith(ingress):
                environ['PATH_INFO'] = path_info[len(ingress):]
        return self.app(environ, start_response)


def create_app():
    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.secret_key = os.environ.get('FLASK_SECRET', 'hepburn-dev-secret-change-me')
    app.wsgi_app = IngressMiddleware(app.wsgi_app)

    # Make cfg available to every template
    @app.context_processor
    def inject_config():
        return {
            'cfg': {
                'family_name': os.environ.get('FAMILY_NAME', 'Hepburn'),
                'primary_user': os.environ.get('PRIMARY_USER', ''),
                'secondary_user': os.environ.get('SECONDARY_USER', ''),
                'ai_provider': os.environ.get('AI_PROVIDER', 'none'),
            }
        }

    app.register_blueprint(bp)
    return app


def seed_initial_accounts():
    """No-op as of v0.5.1.

    The dashboard now ships with no demo data. Users start with an empty
    database and add their own accounts via the UI. This is the right
    behaviour for a public repository.

    Anyone running an older install with seeded demo data can use the
    cleanup tool at /admin/cleanup to remove it.
    """
    return


def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    init_db()
    seed_initial_accounts()

    # Push state to HA as native sensors (no-op outside HA addon environment)
    try:
        from app.ha_sensors import start_periodic_push
        start_periodic_push(interval_seconds=300)
    except Exception as e:
        logging.warning(f'HA sensor push thread did not start: {e}')

    app = create_app()
    port = int(os.environ.get('FINANCE_PORT', 8765))
    logging.info(f'Starting Hepburn Finance on port {port}')
    app.run(host='0.0.0.0', port=port, debug=False)


if __name__ == '__main__':
    main()
