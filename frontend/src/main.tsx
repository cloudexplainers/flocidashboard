import React from 'react';
import ReactDOM from 'react-dom/client';
import '@cloudscape-design/global-styles/index.css';
import './index.css';
import { SessionProvider } from './components/SessionContext';
import { App } from './App';

const rootElement = document.getElementById('root');
if (rootElement) {
  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <SessionProvider><App /></SessionProvider>
    </React.StrictMode>
  );
}
