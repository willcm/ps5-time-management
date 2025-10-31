"""Static file serving routes for PS5 Time Management add-on"""
import os
import logging
from flask import send_from_directory

logger = logging.getLogger(__name__)

# These will be set by main.py via register_routes
app = None


def register_routes(flask_app):
    """Register static file routes with Flask app"""
    global app
    app = flask_app
    
    @app.route('/images/<path:filename>')
    def serve_cached_image(filename):
        """Serve cached game images from /data/game_images"""
        try:
            directory = '/data/game_images'
            full_path = os.path.join(directory, filename)
            if os.path.exists(full_path):
                logger.debug(f"Serving cached image: {full_path}")
                return send_from_directory(directory, filename)
            else:
                logger.warning(f"Requested image not found on disk: {full_path}")
                return "", 404
        except Exception as e:
            logger.error(f"Image serve error for {filename}: {e}")
            return "", 404

    @app.route('/stats/<user>/image/<path:filename>')
    def serve_stats_scoped_image(user, filename):
        """Ingress-safe image URL under the stats namespace; proxies to cached image server."""
        try:
            return serve_cached_image(filename)
        except Exception as e:
            logger.error(f"Stats-scoped image serve error for {filename}: {e}")
            return "", 404

    @app.route('/ps5.svg')
    def serve_ps5_svg():
        """Serve the PS5 SVG icon"""
        try:
            svg_path = os.path.join('/app', 'ps5.svg')
            if os.path.exists(svg_path):
                with open(svg_path, 'r', encoding='utf-8') as f:
                    return f.read(), 200, {'Content-Type': 'image/svg+xml'}
            else:
                return "", 404
        except Exception as e:
            logger.error(f"SVG serve error: {e}")
            return "", 404

    @app.route('/globals.css')
    def globals_css():
        try:
            return send_from_directory('templates', 'globals.css')
        except Exception as e:
            logger.error(f"Failed to serve globals.css: {e}")
            return '', 404

