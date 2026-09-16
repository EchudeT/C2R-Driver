from enum import StrEnum


class RepositoryRole(StrEnum):
    SOURCE = "source"
    TARGET = "target"
    QEMU = "qemu"

    @property
    def sequence(self) -> int:
        return (
            RepositoryRole.SOURCE,
            RepositoryRole.TARGET,
            RepositoryRole.QEMU,
        ).index(self)
