/**
 * WebSocket Service - Socket.IO Event Documentation
 * 
 * This file documents all available WebSocket events for the Solgri system
 */

/**
 * Client -> Server Events
 */

// Subscribe to live system updates
export const subscribe_updates = 'subscribe_updates';

// Send a message to the backend
export const message = 'message';

/**
 * Server -> Client Events
 */

// Connection confirmation
export const connect = 'connect';

// Disconnection event
export const disconnect = 'disconnect';

// Response from backend
export const response = 'response';

// Live state updates from the grid
export const state_update = 'state_update';

// Error events
export const error = 'error';

/**
 * State Update Data Structure
 */
export interface StateUpdateData {
  timestamp: number; // Unix timestamp
  nodes: Node[];
  weather: Weather;
  grid: GridStatus;
}

export interface Node {
  type: 'gen' | 'battery' | 'load';
  status: 1 | 0; // 1 = active, 0 = inactive
  output: number; // Power output in watts
  charge_level?: number; // For battery nodes
  priority?: number; // For load nodes
}

export interface Weather {
  rain: number;
  sunlight: number; // 0-100 percentage
  clouds: number; // 0-100 percentage
}

export interface GridStatus {
  status: 1 | 0; // 1 = online, 0 = offline
  output: number; // Power output in watts
  carbon_intensity: number; // grams per kWh
}
