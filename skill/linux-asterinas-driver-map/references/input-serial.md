# Migration comparison: input devices and UART

Read this when the contract, implementation, or review concerns this topic. Platform facts and source line numbers are maintained separately in [Linux](linux/input-serial.md) and [Asterinas](asterinas/input-serial.md); this entry does not copy the source tables.

First confirm the specific protocol and scope: the keyboard/mouse event layer is not the controller, and a UART console is not a complete tty. Map event boundaries, errors/retries, FIFO behavior, and callback context item by item; a similar upper-layer interface does not prove that the hardware works.

## Suggested validation

For input, check the complete event sequence, truncated or malformed input, and backlog. For UART, check FIFO boundaries, transmitted and received content, error status, and notification recovery. Test only capabilities agreed in the contract.
