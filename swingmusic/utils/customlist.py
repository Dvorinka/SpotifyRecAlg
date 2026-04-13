from collections.abc import Iterator


class CustomList(list):
    """
    A custom list implementation with hooks for future shared memory support.

    This list can be used as a drop-in replacement for standard lists.
    Future enhancement: implement SharedMemoryList for inter-process
    communication without serialization overhead.
    """

    def __getitem__(self, index):
        # Hook for shared memory operations
        return super().__getitem__(index)

    def __iter__(self) -> Iterator:
        # Hook for shared memory operations
        return super().__iter__()
