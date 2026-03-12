type MessageHandler = (data: any) => void;

export class ScreenMirrorWS {
  private ws: WebSocket | null = null;
  private onFrame: MessageHandler;
  private onError: MessageHandler;
  private reconnectTimer: number | null = null;

  constructor(onFrame: MessageHandler, onError: MessageHandler = () => {}) {
    this.onFrame = onFrame;
    this.onError = onError;
  }

  connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws/screen`;
    this.ws = new WebSocket(url);

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'frame') {
        this.onFrame(data);
      } else if (data.type === 'error') {
        this.onError(data);
      }
    };

    this.ws.onclose = () => {
      this.reconnectTimer = window.setTimeout(() => this.connect(), 2000);
    };
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }
    this.ws?.close();
    this.ws = null;
  }
}

export class PlaybackWS {
  private ws: WebSocket | null = null;
  private onStepResult: MessageHandler;
  private onComplete: MessageHandler;
  private onError: MessageHandler;

  constructor(
    onStepResult: MessageHandler,
    onComplete: MessageHandler,
    onError: MessageHandler = () => {},
  ) {
    this.onStepResult = onStepResult;
    this.onComplete = onComplete;
    this.onError = onError;
  }

  connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws/playback`;
    this.ws = new WebSocket(url);

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'step_result') {
        this.onStepResult(data.data);
      } else if (data.type === 'playback_complete') {
        this.onComplete(data);
      } else if (data.type === 'error') {
        this.onError(data);
      }
    };
  }

  play(scenarioName: string, verify = true) {
    this.ws?.send(JSON.stringify({ action: 'play', scenario: scenarioName, verify }));
  }

  stop() {
    this.ws?.send(JSON.stringify({ action: 'stop' }));
  }

  disconnect() {
    this.ws?.close();
    this.ws = null;
  }
}
