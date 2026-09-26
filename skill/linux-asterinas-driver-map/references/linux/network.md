# Linux: network devices

Use NE2000/8390 as the complete example; replace the entry points from the target device's own operations table for another NIC. This is not a generic NAPI-driver template.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [drivers/net/ethernet/8390/ne2k-pci.c:200–213][C1] | `ne2k_netdev_ops`: actual bindings for `ndo_open/stop/start_xmit/timeout`. |
| C2 | [drivers/net/ethernet/8390/8390.c:7–45][C2] | Wrapper includes `lib8390.c` and maps `ei_*` interfaces to `__ei_*`; do not treat wrapper lines as core behavior. |
| C3 | [drivers/net/ethernet/8390/ne2k-pci.c:374–387][C3] | Assignments for reset/block_input/block_output/get_8390_hdr callbacks and netdev registration. |
| C4 | [drivers/net/ethernet/8390/lib8390.c:302–413][C4] | `__ei_start_xmit`: short-frame padding, `page_lock`, two TX slots, BUSY, and skb consumption. |
| C5 | [drivers/net/ethernet/8390/lib8390.c:427–507][C5] | `__ei_interrupt`: shared interrupt handling, RX/TX/overflow dispatch, and ack; verify the return value. |
| C6 | [drivers/net/ethernet/8390/lib8390.c:659–777][C6] | `ei_receive`: page-ring bounds, length/status, block_input, `netif_rx`, and next-page update. |
| C7 | [drivers/net/ethernet/8390/lib8390.c:579–649][C7] | `ei_tx_intr`: TX status and slot advancement; confirm how backpressure is released. |
| C8 | [drivers/net/ethernet/8390/ne2k-pci.c:436–455][C8] | `open` requests the shared IRQ and then calls `ei_open`; `close` calls `ei_close` before `free_irq`. |
| C9 | [drivers/net/ethernet/8390/lib8390.c:95–100][C9] | `ei_block_output/input/get_8390_hdr` macros expand to function pointers in `ei_local`. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | Register bits, device-status fields, or page layout are needed | [drivers/net/ethernet/8390/8390.h][Q1]: check constants and private structures with the concrete front end; promote it to core when required for this data path. |
| Q2 | Actual port transfer or reset must be checked | [drivers/net/ethernet/8390/ne2k-pci.c][Q2]: continue through block_input/output/reset; see the precise path in the same-platform I/O entry. |
| Q3 | The NIC changes, NAPI is used, or network-stack caller conditions must be proven | [net/core/dev.c][Q3]: trace callers of the current operations table; do not equate this example's `netif_rx` with every RX-delivery path. |

## Verified core paths

1. **TX**: `ndo_start_xmit=ei_start_xmit` [C1]→wrapper [C2]→`__ei_start_xmit` [C4]→`ei_block_output` macro [C9]→bound `ne2k_pci_block_output` [C3]. An idle device triggers transmission; a busy device retains queue state. Check that the BUSY branch retains the skb and when the successful branch consumes it.
2. **Hardware completion is asynchronous**: `open` registers `ei_interrupt` [C8], wrapper [C2]→`__ei_interrupt` [C5]→`ei_tx_intr` [C7]. Do not treat successful TX submission as transmission completed.
3. **RX**: interrupt dispatch [C5]→`ei_receive` [C6]→get-header/block-input function pointer [C9], [C3]→length/status check→`netif_rx`; check wraparound, abnormal next page, and the boundary after allocation failure.
4. **Shutdown**: `close`'s `ei_close`→`free_irq` [C8]; final remove is supplied by the PCI entry. Overflow/timeout recovery must continue into the corresponding `lib8390.c` branches; normal traffic alone is insufficient.

## Reading endpoint and boundary

Stop after explaining RX/TX buffer ownership, length/ring boundaries, queue stop/wake, and IRQ/shutdown races. Device-specific protocol still requires the concrete front end and hardware basis; reading this example does not mark other NICs fully covered.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/ne2k-pci.c#L200-L213
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/8390.c#L7-L45
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/ne2k-pci.c#L374-L387
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/lib8390.c#L302-L413
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/lib8390.c#L427-L507
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/lib8390.c#L659-L777
[C7]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/lib8390.c#L579-L649
[C8]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/ne2k-pci.c#L436-L455
[C9]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/lib8390.c#L95-L100
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/8390.h
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/ne2k-pci.c
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/net/core/dev.c
