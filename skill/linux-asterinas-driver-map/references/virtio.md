# Migration comparison: VirtIO devices and transport

Read this when the contract, implementation, or review concerns this topic. Platform facts and source line numbers are maintained separately in [Linux](linux/virtio.md) and [Asterinas](asterinas/virtio.md); this entry does not copy the source tables.

The actual protocol versions, features, ring, and transport on both sides must be compatible. Check buffer direction, publication/notification order, and abnormal completion; deleting a queue object does not mean that the device has stopped DMA.

## Suggested validation

Test negotiation refusal, queue-full and recycling behavior, wraparound, abnormal tokens or short responses, notification boundaries, and rebuilding after reset. Report only failures that the test environment can actually inject.
