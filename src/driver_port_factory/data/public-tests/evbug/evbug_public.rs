// SPDX-License-Identifier: MPL-2.0
// Operator-owned public test: actual target input framework, synthetic input devices.
// Adapted from driver-port-lab's public evbug lifecycle fixture; contains no port implementation.
use alloc::sync::Arc;

use crate::{
    event_type_codes::{EventTypes, KeyCode, KeyStatus, RelCode, SynEvent},
    input_dev::{InputCapability, InputDevice, InputEvent, InputId},
};

#[derive(Debug)]
struct PublicDevice {
    name: &'static str,
    capability: InputCapability,
}
impl InputDevice for PublicDevice {
    fn name(&self) -> &str {
        self.name
    }
    fn phys(&self) -> &str {
        "dpf/public-input"
    }
    fn uniq(&self) -> &str {
        self.name
    }
    fn id(&self) -> InputId {
        InputId::new(InputId::BUS_VIRTUAL, 0, 0, 1)
    }
    fn capability(&self) -> &InputCapability {
        &self.capability
    }
}
fn device(name: &'static str) -> Arc<dyn InputDevice> {
    let mut capability = InputCapability::new();
    capability.set_supported_event_type(EventTypes::SYN);
    capability.set_supported_key(KeyCode::A);
    capability.set_supported_relative_axis(RelCode::X);
    capability.set_supported_relative_axis(RelCode::Y);
    Arc::new(PublicDevice { name, capability })
}

/// Called by the thin /proc/evbug_test adapter after normal input component initialization.
/// Each scenario executes once in a fresh VM. Only this fixed fixture emits the DONE marker.
pub fn run(scenario: &str) -> Result<(), &'static str> {
    match scenario {
        "events" => events(),
        "lifecycle" => lifecycle(),
        "boundaries" => boundaries(),
        _ => return Err("unknown public scenario"),
    }
    ostd::early_println!("DPF_EVBUG_DONE {}", scenario);
    Ok(())
}

fn events() {
    let dev = device("dpf-events");
    let handle = crate::register_device(dev.clone());
    let handlers = handle.count_handlers();
    let owners = Arc::strong_count(&dev);
    let registration = crate::evbug::register();
    assert_eq!(handle.count_handlers(), handlers + 1);
    let events = [
        InputEvent::Key(KeyCode::A, KeyStatus::Pressed),
        InputEvent::Relative(RelCode::X, -7),
        InputEvent::Sync(SynEvent::Report),
    ];
    handle.submit_events(&events);
    drop(registration);
    assert_eq!(handle.count_handlers(), handlers);
    assert_eq!(Arc::strong_count(&dev), owners);
    // Host's exact ordered event assertion also rejects output after handler removal.
    handle.submit_events(&events);
    drop(handle);
}

fn lifecycle() {
    let devices = crate::count_devices();
    let classes = crate::count_handler_class();
    let early = device("dpf-early");
    let early_handle = crate::register_device(early.clone());
    let old_handlers = early_handle.count_handlers();
    let old_owners = Arc::strong_count(&early);
    let registration = crate::evbug::register();
    assert_eq!(crate::count_handler_class(), classes + 1);
    assert_eq!(early_handle.count_handlers(), old_handlers + 1);
    early_handle.submit_events(&[InputEvent::Key(KeyCode::A, KeyStatus::Pressed)]);
    let late_handle = crate::register_device(device("dpf-late"));
    assert_eq!(crate::count_devices(), devices + 2);
    late_handle.submit_events(&[InputEvent::Relative(RelCode::Y, 5)]);
    drop(late_handle);
    assert_eq!(crate::count_devices(), devices + 1);
    drop(registration);
    assert_eq!(crate::count_handler_class(), classes);
    assert_eq!(early_handle.count_handlers(), old_handlers);
    assert_eq!(Arc::strong_count(&early), old_owners);
    early_handle.submit_events(&[InputEvent::Key(KeyCode::A, KeyStatus::Released)]);
    drop(early_handle);
    assert_eq!(crate::count_devices(), devices);
}

fn boundaries() {
    let handle = crate::register_device(device("dpf-boundaries"));
    let handlers = handle.count_handlers();
    let classes = crate::count_handler_class();
    let registration = crate::evbug::register();
    handle.submit_events(&[]);
    let events = [
        InputEvent::Relative(RelCode::X, i32::MIN),
        InputEvent::Relative(RelCode::Y, i32::MAX),
        InputEvent::Sync(SynEvent::Config),
        InputEvent::Key(KeyCode::A, KeyStatus::Released),
    ];
    handle.submit_events(&events);
    drop(registration);
    assert_eq!(handle.count_handlers(), handlers);
    handle.submit_events(&events);
    let again = crate::evbug::register();
    assert_eq!(handle.count_handlers(), handlers + 1);
    handle.submit_events(&[InputEvent::Sync(SynEvent::Report)]);
    drop(again);
    assert_eq!(handle.count_handlers(), handlers);
    assert_eq!(crate::count_handler_class(), classes);
    drop(handle);
}
