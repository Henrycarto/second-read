"""Two roles, implemented as Django groups so admins manage them without code."""

ANNOTATOR = "annotator"
REVIEWER = "reviewer"


def has_role(user, role: str) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=role).exists()


def is_annotator(user) -> bool:
    return has_role(user, ANNOTATOR)


def is_reviewer(user) -> bool:
    return has_role(user, REVIEWER)
