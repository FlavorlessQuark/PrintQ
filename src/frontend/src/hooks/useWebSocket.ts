import { stat } from 'fs';
import { useEffect, useState, useCallback, useRef } from 'react';
import io, { Socket } from 'socket.io-client';

interface Time_State {
  timestamp: number;
  nodes: any[];
  weather: any;
  grid: any;
  explanation?: string;
}


interface Data {
    period: number;
    data: Time_State;
}

type State = Array<Data>

export const useWebSocket = (url: string = 'http://localhost:5000') => {
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [state,setState] = useState<State | null>(null);
  const [error, setError] = useState<string | null>(null);
  const socketRef = useRef<Socket | null>(null);

  useEffect(() => {
    // Initialize socket connection
    const socket = io(url, {
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      reconnectionAttempts: 5,
    });

    socketRef.current = socket;

    // Connection event
    socket.on('connect', () => {
      console.log('WebSocket connected');
      setIsConnected(true);
      setError(null);
      // Subscribe to updates after connecting
      socket.emit('subscribe_updates', { message: 'Subscribe to live updates' });
    });

    // Disconnect event
    socket.on('disconnect', () => {
      console.log('WebSocket disconnected');
      setIsConnected(false);
    });

    // State update event
    socket.on('state_update', (data: Data) => {
      let id = data.period;
      if (state)
        setState({...state, [id]: data});
    });

    // Data response event (for time-period based data)
    socket.on('data_all', (data: State) => {
      console.log('Data response received:', data);
      setState(data);
    });

    // Response event
    socket.on('response', (data: any) => {
      console.log('Response received:', data);
    });

    // Error event
    socket.on('error', (error: any) => {
      console.error('WebSocket error:', error);
      setError(error);
    });

    // Cleanup on unmount
    return () => {
      socket.disconnect();
    };
  }, [url]);

  // Function to send message
  const sendMessage = useCallback((event: string, data: any) => {
    if (socketRef.current && isConnected) {
      socketRef.current.emit(event, data);
    }
  }, [isConnected]);

  return {
    isConnected,
    state,
    error,
    sendMessage,
  };
};
