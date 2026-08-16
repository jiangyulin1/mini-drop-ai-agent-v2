import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { dirname } from "node:path";

/** Durable, compact JSONL spool. Records disappear only after Server ACK. */
export class EventSpool {
  constructor(path) {
    this.path = path;
    this.records = new Map();
    if (path && existsSync(path)) {
      for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
        if (!line.trim()) continue;
        try {
          const record = JSON.parse(line);
          if (record?.idempotency_key) this.records.set(record.idempotency_key, record);
        } catch {
          // A torn final line is ignored; acknowledged records are never restored.
        }
      }
    }
  }

  has(key) {
    return this.records.has(key);
  }

  get(key) {
    return this.records.get(key) || null;
  }

  append(record) {
    if (this.records.has(record.idempotency_key)) return false;
    if (this.path) {
      mkdirSync(dirname(this.path), { recursive: true });
      appendFileSync(this.path, `${JSON.stringify(record)}\n`, "utf8");
    }
    this.records.set(record.idempotency_key, record);
    return true;
  }

  pending(caseId = null) {
    return [...this.records.values()]
      .filter((record) => !caseId || record.case_id === caseId)
      .sort((left, right) => left.event_seq - right.event_seq);
  }

  ack(key) {
    if (!this.records.delete(key)) return false;
    this._rewrite();
    return true;
  }

  _rewrite() {
    if (!this.path) return;
    mkdirSync(dirname(this.path), { recursive: true });
    const temporary = `${this.path}.tmp`;
    const body = [...this.records.values()]
      .map((record) => JSON.stringify(record))
      .join("\n");
    writeFileSync(temporary, body ? `${body}\n` : "", "utf8");
    renameSync(temporary, this.path);
  }
}
