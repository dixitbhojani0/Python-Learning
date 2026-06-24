import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { ChatRequest, ChatResponse } from '../models/api.models';

@Injectable({ providedIn: 'root' })
export class ChatService {
  private base = environment.apiUrl;

  constructor(private http: HttpClient) {}

  sendMessage(body: ChatRequest): Observable<ChatResponse> {
    return this.http.post<ChatResponse>(`${this.base}/api/chat`, body);
  }

  // SSE streaming — EventSource is a browser Web API that handles chunked text/event-stream.
  // We wrap it in an Observable so components can subscribe/unsubscribe cleanly.
  streamResponse(streamId: string): Observable<MessageEvent> {
    return new Observable(observer => {
      const url = `${this.base}/api/stream/${streamId}`;
      const es = new EventSource(url);
      es.onmessage = (ev) => observer.next(ev);
      es.onerror = (err) => { observer.error(err); es.close(); };
      return () => es.close();
    });
  }
}
