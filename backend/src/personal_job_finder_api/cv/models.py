"""Validated candidate profile extracted from the master CV.

These models are the contract for three callers: the AI extraction prompt, the
SQLite ``profile_json`` column, and the Settings editor. Keeping them strict
about shape but tolerant about missing values means a sparse CV still parses,
while a hallucinated structure fails loudly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContactInfo(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None


class WorkExperience(BaseModel):
    company: str
    title: str
    location: str | None = None
    start_date: str
    end_date: str | None = None
    is_current: bool = False
    achievements: list[str] = Field(default_factory=list)


class Education(BaseModel):
    institution: str
    degree: str
    field_of_study: str
    start_year: str | None = None
    end_year: str | None = None
    gpa: str | None = None


class Project(BaseModel):
    name: str
    description: str
    tech_stack: list[str] = Field(default_factory=list)
    url: str | None = None


class SkillsCollection(BaseModel):
    hard_skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    contact_info: ContactInfo
    summary: str = ""
    skills: SkillsCollection = Field(default_factory=SkillsCollection)
    experience: list[WorkExperience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
