"""
Rutas públicas de publicaciones (parche MX). Sin login: Traefik las deja pasar sin forwardAuth.
Solo leen la copia congelada de uploads/shares/<token>/; nunca tocan proyectos, simulaciones ni Zep.
"""
from datetime import datetime

from flask import Blueprint, abort, jsonify, render_template, request, send_file

from ..services.share import ShareService

public_bp = Blueprint('public', __name__)


def _base_url() -> str:
    proto = request.headers.get('X-Forwarded-Proto', 'https')
    host = request.headers.get('X-Forwarded-Host') or request.host
    return f"{proto}://{host}"


def _meta_or_404(token: str):
    meta = ShareService.get(token)
    if not meta:
        abort(404)
    return meta


@public_bp.route('/p/<token>', methods=['GET'])
def public_page(token: str):
    meta = _meta_or_404(token)
    base = _base_url()
    try:
        fecha = datetime.fromisoformat(meta.get('created_at', '')).strftime('%d/%m/%Y')
    except ValueError:
        fecha = ''
    has_graph = ShareService.file_path(token, 'graph.json') is not None
    html = render_template(
        'public_share.html',
        titular=meta.get('titular') or meta.get('report_title') or 'Simulación de escenarios',
        descripcion=(meta.get('whatsapp') or '')[:280],
        whatsapp=meta.get('whatsapp', ''),
        puntos=meta.get('puntos', []),
        report_title=meta.get('report_title', ''),
        caption=meta.get('caption', ''),
        has_image=meta.get('has_image', False),
        image_url=f"/api/public/{token}/image.png",
        preview_url=f"{base}/api/public/{token}/preview.jpg",
        page_url=f"{base}/p/{token}",
        has_graph=has_graph,
        graph_url=f"/api/public/{token}/graph",
        stats=meta.get('graph_stats', {}),
        entity_types=meta.get('entity_types', []),
        fecha=fecha,
    )
    resp = public_page_response(html)
    return resp


def public_page_response(html: str):
    from flask import make_response
    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    resp.headers['Cache-Control'] = 'public, max-age=300'
    resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    return resp


@public_bp.route('/api/public/<token>', methods=['GET'])
def public_meta(token: str):
    return jsonify({"success": True, "data": ShareService.public_meta(_meta_or_404(token))})


@public_bp.route('/api/public/<token>/graph', methods=['GET'])
def public_graph(token: str):
    _meta_or_404(token)
    path = ShareService.file_path(token, 'graph.json')
    if not path:
        abort(404)
    resp = send_file(path, mimetype='application/json', max_age=3600)
    return resp


@public_bp.route('/api/public/<token>/image.png', methods=['GET'])
def public_image(token: str):
    _meta_or_404(token)
    path = ShareService.file_path(token, 'illustration.png')
    if not path:
        abort(404)
    return send_file(path, mimetype='image/png', max_age=86400)


@public_bp.route('/api/public/<token>/preview.jpg', methods=['GET'])
def public_preview(token: str):
    _meta_or_404(token)
    path = ShareService.file_path(token, 'preview.jpg') or ShareService.file_path(token, 'illustration.png')
    if not path:
        abort(404)
    return send_file(path, mimetype='image/jpeg' if path.endswith('.jpg') else 'image/png', max_age=86400)
