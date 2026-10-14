"""Authenticated local application-workshop endpoints; no execution endpoints."""
from flask import Blueprint, current_app, jsonify, request

from ..workbench.routes import json_body

app_workshop = Blueprint("app_workshop", __name__, url_prefix="/desktop/api/app-workshop")


def context():
    owner = current_app.extensions["workbench_service"].authenticate(request.headers.get("Cookie", ""))["user"]["id"]
    return current_app.extensions["app_workshop"], owner


@app_workshop.post("/sessions")
def create_session():
    service, owner = context()
    return jsonify(service.analyze(owner, json_body())), 201


@app_workshop.get("/sessions/current")
def current_session():
    service, owner = context()
    return jsonify({"session": service.store.current(owner)})


@app_workshop.get("/sessions/<session_id>")
def get_session(session_id):
    service, owner = context()
    return jsonify(service.session(owner, session_id))


@app_workshop.post("/sessions/<session_id>/refine")
def refine(session_id):
    service, owner = context()
    return jsonify(service.refine(owner, session_id, json_body()))


@app_workshop.post("/sessions/<session_id>/create")
def create_draft(session_id):
    service, owner = context()
    return jsonify(service.create(owner, session_id, json_body()))


@app_workshop.post("/sessions/<session_id>/save")
def save_draft(session_id):
    service, owner = context()
    return jsonify(service.save(owner, session_id, json_body()))


@app_workshop.post("/sessions/<session_id>/handoff")
def handoff(session_id):
    service, owner = context()
    return jsonify(service.handoff(owner, session_id, json_body()))
