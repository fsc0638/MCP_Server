"""Skill management routes (migration bridge)."""

from fastapi import APIRouter, File, Form, UploadFile

from router import (
    SkillUpdateRequest,
    CreateSkillRequest,
    list_skills as legacy_list_skills,
    get_skill as legacy_get_skill,
    update_skill as legacy_update_skill,
    delete_skill as legacy_delete_skill,
    rollback_skill as legacy_rollback_skill,
    install_skill_deps as legacy_install_skill_deps,
    upload_skill_file as legacy_upload_skill_file,
    get_skill_files as legacy_get_skill_files,
    delete_skill_file as legacy_delete_skill_file,
    rescan_skills as legacy_rescan_skills,
    create_skill as legacy_create_skill,
)

router = APIRouter(tags=["Skill Management"])


@router.get("/skills/list")
def list_skills():
    return legacy_list_skills()


@router.get("/skills/{skill_name}")
def get_skill(skill_name: str):
    return legacy_get_skill(skill_name)


@router.put("/skills/{skill_name}")
def update_skill(skill_name: str, req: SkillUpdateRequest):
    return legacy_update_skill(skill_name, req)


@router.delete("/skills/{skill_name}")
def delete_skill(skill_name: str):
    return legacy_delete_skill(skill_name)


@router.post("/skills/{skill_name}/rollback")
def rollback_skill(skill_name: str):
    return legacy_rollback_skill(skill_name)


@router.post("/skills/{skill_name}/install")
def install_skill_deps(skill_name: str):
    return legacy_install_skill_deps(skill_name)


@router.post("/skills/{skill_name}/upload")
async def upload_skill_file(skill_name: str, file: UploadFile = File(...), file_type: str = Form(...)):
    return await legacy_upload_skill_file(skill_name, file, file_type)


@router.get("/skills/{skill_name}/files")
async def get_skill_files(skill_name: str):
    return await legacy_get_skill_files(skill_name)


@router.delete("/skills/{skill_name}/files/{folder}/{filename}")
async def delete_skill_file(skill_name: str, folder: str, filename: str):
    return await legacy_delete_skill_file(skill_name, folder, filename)


@router.post("/skills/rescan")
def rescan_skills():
    return legacy_rescan_skills()


@router.post("/skills/create")
def create_skill(req: CreateSkillRequest):
    return legacy_create_skill(req)

