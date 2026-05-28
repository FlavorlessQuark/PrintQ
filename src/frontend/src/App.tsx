import React from 'react';
import { useEffect } from 'react';
import './App.css';
import { useWebSocket } from './hooks/useWebSocket';
import GridVisualization from './components/GridVisualization';
import { Node } from './components/GridVisualization';

interface Data {
  nodes: Array<Node>;
  weather: {
    rain: number;
    sunlight: number;
    clouds: number;
  };
  grid: {
    status: number;
    output: number;
    carbon_intensity: number;
  };
  explanation?: string;
}
const App = () => {
  const { isConnected, state, error, sendMessage } = useWebSocket(
    'http://localhost:5000'
  );

  const handleSendMessage = () => {
    sendMessage('start', { text: '' });
  };


  return (
    <div className="App">
      <div className="App-container">
        {/* Header */}
        <div className="App-header-top">
          <h1>PrintQ</h1>
          
          {/* Connection Status */}

              {/* Test Button */}
              <button onClick={handleSendMessage} disabled={!isConnected} className="test-button">
                Send Test Message
              </button>
            </div>
          </div>

        {/* Loading State */}
        {!state && (
          <div className="loading-state">
            <p>Waiting for system data...</p>
          </div>
        )}
      </div>
  );
}

export default App;
