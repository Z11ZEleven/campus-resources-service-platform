class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def not_found() -> DomainError:
    return DomainError(404, "NOT_FOUND", "资源不存在或不可访问")


def conflict(message: str) -> DomainError:
    return DomainError(409, "CONFLICT", message)


def forbidden() -> DomainError:
    return DomainError(403, "FORBIDDEN", "没有操作权限")
