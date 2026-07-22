# Mini-Drop Diagnosis Golden Evaluation

- Scenarios: 7
- Passed: 7
- Classification accuracy: 100.00%
- Evidence reference integrity: 100.00%
- Unsafe auto execute: 0

| Scenario | Result | Classification | Verification |
|---|---|---|---|
| downstream_cpu_hotspot | PASS | downstream_dependency | passed |
| memory_leak | PASS | self_code_or_process_pressure | passed |
| mysql_lock_wait | PASS | insufficient_evidence | passed |
| network_packet_loss | PASS | self_code_or_process_pressure | passed |
| same_host_cpu_noise | PASS | same_host_noisy_neighbor | passed |
| self_code_hotspot | PASS | self_code_or_process_pressure | passed |
| shared_io_contention | PASS | host_resource_contention | passed |
