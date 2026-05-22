import { FetchLike, InferenceEvent, SendBeaconLike } from "./types";

interface BatchedEventShipperOptions {
  endpoint: string;
  batchSize: number;
  flushIntervalMs: number;
  queueMax: number;
  maxRetries: number;
  fetchImpl: FetchLike;
  sendBeacon: SendBeaconLike | undefined;
}

export class BatchedEventShipper {
  private readonly endpoint: string;
  private readonly batchSize: number;
  private readonly queueMax: number;
  private readonly maxRetries: number;
  private readonly fetchImpl: FetchLike;
  private readonly sendBeacon: SendBeaconLike | undefined;
  private readonly timer: ReturnType<typeof setInterval>;
  private readonly queue: InferenceEvent[] = [];
  private dropped = 0;
  private inFlight: Promise<void> | null = null;

  constructor(options: BatchedEventShipperOptions) {
    this.endpoint = options.endpoint;
    this.batchSize = options.batchSize;
    this.queueMax = options.queueMax;
    this.maxRetries = options.maxRetries;
    this.fetchImpl = options.fetchImpl;
    this.sendBeacon = options.sendBeacon;
    this.timer = setInterval(() => {
      void this.flush();
    }, options.flushIntervalMs);
  }

  get droppedEvents(): number {
    return this.dropped;
  }

  enqueue(event: InferenceEvent): void {
    if (this.queue.length >= this.queueMax) {
      this.queue.shift();
      this.dropped += 1;
    }
    this.queue.push(event);
    if (this.queue.length >= this.batchSize) {
      void this.flush();
    }
  }

  async flush(): Promise<void> {
    if (this.inFlight !== null) {
      await this.inFlight;
      return;
    }

    this.inFlight = this.flushBatches();
    try {
      await this.inFlight;
    } finally {
      this.inFlight = null;
    }
  }

  async close(): Promise<void> {
    clearInterval(this.timer);
    await this.flush();
  }

  flushWithBeacon(): boolean {
    if (this.queue.length === 0 || this.sendBeacon === undefined) {
      return false;
    }
    const batch = this.queue.splice(0, this.queue.length);
    const payload = JSON.stringify({ events: batch });
    const accepted = this.sendBeacon(this.endpoint, payload);
    if (!accepted) {
      this.queue.unshift(...batch);
    }
    return accepted;
  }

  private async flushBatches(): Promise<void> {
    while (this.queue.length > 0) {
      const batch = this.queue.splice(0, this.batchSize);
      const delivered = await this.sendBatchWithRetry(batch);
      if (!delivered) {
        this.dropped += batch.length;
      }
    }
  }

  private async sendBatchWithRetry(batch: InferenceEvent[]): Promise<boolean> {
    for (let attempt = 0; attempt <= this.maxRetries; attempt += 1) {
      try {
        const response = await this.fetchImpl(this.endpoint, {
          method: "POST",
          headers: {
            "content-type": "application/json",
          },
          body: JSON.stringify({ events: batch }),
        });
        if (response.ok) {
          return true;
        }
      } catch {
        // Best-effort retry loop. Failures become dropped events after the cap.
      }

      if (attempt === this.maxRetries) {
        return false;
      }
      await wait(retryDelay(attempt));
    }
    return false;
  }
}

function retryDelay(attempt: number): number {
  return Math.min(1000, 100 * 2 ** attempt);
}

function wait(delayMs: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, delayMs);
  });
}
