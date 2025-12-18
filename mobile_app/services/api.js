import axios from 'axios';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { API_BASE_URL, API_ENDPOINTS } from '../config';

// Connection state management - start optimistic
let connectionState = {
  isOnline: true, // Start as online (optimistic)
  lastCheck: null,
  quality: 'good', // Start with 'good' instead of 'unknown'
  latency: null,
  consecutiveFailures: 0, // Track consecutive failures
  lastSuccess: Date.now(), // Start with current time as last success
};

// Request queue for offline scenarios
const requestQueue = [];
let isProcessingQueue = false;

// Separate connection monitoring tunnel
let connectionMonitorInterval = null;
let isMonitoring = false;
const MONITOR_INTERVAL = 10000; // Check every 10 seconds
const MAX_CONSECUTIVE_FAILURES = 3; // Only mark offline after 3 consecutive failures

// Create axios instance with default config
// Use dynamic baseURL to handle connection issues better
const getBaseURL = () => {
  // Always use the configured API_BASE_URL
  return API_BASE_URL;
};

// Enhanced timeout based on connection quality
const getTimeout = () => {
  switch (connectionState.quality) {
    case 'excellent':
      return 15000;
    case 'good':
      return 20000;
    case 'poor':
      return 30000;
    default:
      return 25000;
  }
};

const api = axios.create({
  baseURL: getBaseURL(),
  timeout: getTimeout(),
  withCredentials: true, // Important for session cookies
  headers: {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
    'Connection': 'keep-alive',
  },
  // Add retry configuration
  validateStatus: function (status) {
    return status < 500; // Don't throw for 4xx errors, only 5xx
  },
  // Enable HTTP keep-alive
  httpAgent: false,
  httpsAgent: false,
});

// Update connection quality based on latency and success
function updateConnectionQuality(latency, success) {
  if (success) {
    // Reset failure counter on success
    connectionState.consecutiveFailures = 0;
    connectionState.lastSuccess = Date.now();
    connectionState.isOnline = true;
    
    if (latency !== null) {
      connectionState.latency = latency;
      
      if (latency < 500) {
        connectionState.quality = 'excellent';
      } else if (latency < 1500) {
        connectionState.quality = 'good';
      } else if (latency < 5000) {
        connectionState.quality = 'poor';
      } else {
        connectionState.quality = 'poor';
      }
    } else {
      // If we got success but no latency, assume good connection
      connectionState.quality = connectionState.quality === 'offline' ? 'good' : connectionState.quality;
    }
  } else {
    // Increment failure counter
    connectionState.consecutiveFailures++;
    
    // Only mark as offline after multiple consecutive failures
    if (connectionState.consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
      connectionState.quality = 'offline';
      connectionState.isOnline = false;
      connectionState.latency = null;
    } else {
      // Keep previous quality if we haven't failed enough times
      // This prevents false offline status from temporary network hiccups
      if (connectionState.quality === 'offline') {
        connectionState.quality = 'poor'; // Upgrade from offline to poor
      }
    }
  }
  
  connectionState.lastCheck = Date.now();
}

// Add request interceptor to include session cookie and update timeout
api.interceptors.request.use(
  async (config) => {
    // Update timeout based on connection quality
    config.timeout = getTimeout();
    
    // Add timestamp for latency measurement
    config.metadata = { startTime: Date.now() };
    
    // Session cookies are handled automatically by axios with withCredentials
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Add response interceptor for error handling and connection quality tracking
api.interceptors.response.use(
  (response) => {
    // Calculate latency
    if (response.config.metadata?.startTime) {
      const latency = Date.now() - response.config.metadata.startTime;
      updateConnectionQuality(latency, true);
    } else {
      // Even without latency, mark as success
      updateConnectionQuality(null, true);
    }
    
    // Update connection state - any response means we're online
    connectionState.isOnline = true;
    connectionState.lastCheck = Date.now();
    connectionState.lastSuccess = Date.now();
    connectionState.consecutiveFailures = 0; // Reset failures
    
    // If quality was offline, upgrade it
    if (connectionState.quality === 'offline') {
      connectionState.quality = 'good';
    }
    
    return response;
  },
  async (error) => {
    // Update connection state on error - but be more lenient
    if (error.code === 'ERR_NETWORK' || !error.response) {
      // Only mark as offline if we've had multiple failures
      // Don't immediately mark offline on single network error
      updateConnectionQuality(null, false);
      // Don't immediately set isOnline to false - let the failure counter handle it
    } else if (error.config?.metadata?.startTime) {
      // If we got a response (even error), server is reachable
      if (error.response) {
        const latency = Date.now() - error.config.metadata.startTime;
        updateConnectionQuality(latency, true);
      } else {
        const latency = Date.now() - error.config.metadata.startTime;
        updateConnectionQuality(latency, false);
      }
    }
    
    if (error.response?.status === 401) {
      // Unauthorized - clear storage and redirect to login
      await AsyncStorage.removeItem('memberData');
      await AsyncStorage.removeItem('sessionId');
    }
    
    // Improve error messages
    if (error.code === 'ECONNABORTED') {
      error.message = 'Request timeout. Please check your connection and try again.';
    } else if (error.code === 'ERR_NETWORK' || !error.response) {
      error.message = 'Network error. Please check your internet connection and server URL.';
    } else if (error.response?.status >= 500) {
      error.message = 'Server error. Please try again later.';
    }
    
    return Promise.reject(error);
  }
);

// Enhanced retry function with exponential backoff
async function retryRequest(requestFn, maxRetries = 3, baseDelay = 1000) {
  let lastError;
  
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await requestFn();
    } catch (error) {
      lastError = error;
      
      // Don't retry on client errors (4xx) except 408 (timeout)
      if (error.response?.status >= 400 && error.response?.status < 500 && error.response?.status !== 408) {
        throw error;
      }
      
      // Don't retry if we've exhausted attempts
      if (attempt >= maxRetries) {
        throw error;
      }
      
      // Calculate exponential backoff delay
      const delay = baseDelay * Math.pow(2, attempt) + Math.random() * 1000; // Add jitter
      await new Promise(resolve => setTimeout(resolve, delay));
    }
  }
  
  throw lastError;
}

// Lightweight connection test - just checks if server responds
const quickConnectionTest = async (timeout = 5000) => {
  const baseURL = getBaseURL();
  const startTime = Date.now();
  
  try {
    // Try health endpoint first (lightweight)
    const healthUrl = baseURL.replace(/\/$/, '') + '/api/mobile/health/';
    
    const response = await axios.get(healthUrl, {
      timeout: timeout,
      validateStatus: () => true, // Accept ANY status code
    });
    
    // If we got ANY response, server is online
    const latency = Date.now() - startTime;
    updateConnectionQuality(latency, true);
    return { connected: true, latency: latency };
  } catch (error) {
    // If we got a response object (even error), server is reachable
    if (error.response) {
      const latency = Date.now() - startTime;
      updateConnectionQuality(latency, true);
      return { connected: true, latency: latency };
    }
    
    // Try alternative lightweight check
    try {
      const testUrl = baseURL.replace(/\/$/, '') + '/admin/';
      await axios.get(testUrl, {
        timeout: timeout,
        validateStatus: () => true,
      });
      const latency = Date.now() - startTime;
      updateConnectionQuality(latency, true);
      return { connected: true, latency: latency };
    } catch (altError) {
      // If we got a response, server is online
      if (altError.response) {
        const latency = Date.now() - startTime;
        updateConnectionQuality(latency, true);
        return { connected: true, latency: latency };
      }
      
      // Only mark as failed if we truly got no response
      updateConnectionQuality(null, false);
      return { connected: false };
    }
  }
};

// Helper function to test API connectivity with health check endpoint
const testConnection = async (maxAttempts = 2) => {
  const baseURL = getBaseURL();
  const startTime = Date.now();
  
  // Use quick test first (faster)
  const quickResult = await quickConnectionTest(8000);
  if (quickResult.connected) {
    return {
      connected: true,
      url: baseURL,
      latency: quickResult.latency,
      quality: connectionState.quality
    };
  }
  
  // If quick test failed, try with more attempts
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      // Try health check endpoint first (most reliable)
      const healthUrl = baseURL.replace(/\/$/, '') + '/api/mobile/health/';
      
      try {
        const response = await axios.get(healthUrl, {
          timeout: 10000,
          validateStatus: () => true, // Accept any status code
        });
        
        const latency = Date.now() - startTime;
        
        // ANY response means server is online
        updateConnectionQuality(latency, true);
        return { 
          connected: true, 
          url: baseURL,
          latency: latency,
          quality: connectionState.quality,
          serverTime: response.data?.server_time
        };
      } catch (healthError) {
        // If we got a response, server is online
        if (healthError.response) {
          const latency = Date.now() - startTime;
          updateConnectionQuality(latency, true);
          return { connected: true, url: baseURL, latency: latency };
        }
        
        // Try alternative endpoints
        const testUrl = baseURL.replace(/\/$/, '') + '/api/mobile/';
        
        try {
          const response = await axios.get(testUrl, {
            timeout: 8000,
            validateStatus: () => true,
          });
          
          const latency = Date.now() - startTime;
          updateConnectionQuality(latency, true);
          return { connected: true, url: baseURL, latency: latency };
        } catch (testError) {
          // If we got a response, server is online
          if (testError.response) {
            const latency = Date.now() - startTime;
            updateConnectionQuality(latency, true);
            return { connected: true, url: baseURL, latency: latency };
          }
          
          // Try Django admin as last resort
          const adminUrl = baseURL.replace(/\/$/, '') + '/admin/';
          try {
            await axios.get(adminUrl, {
              timeout: 8000,
              validateStatus: () => true,
            });
            const latency = Date.now() - startTime;
            updateConnectionQuality(latency, true);
            return { connected: true, url: baseURL, latency: latency };
          } catch (adminError) {
            // If we got ANY response, server is online
            if (adminError.response) {
              const latency = Date.now() - startTime;
              updateConnectionQuality(latency, true);
              return { connected: true, url: baseURL, latency: latency };
            }
            
            // Only mark as failed if truly no response
            if (attempt < maxAttempts) {
              await new Promise(resolve => setTimeout(resolve, 1000 * attempt));
              continue;
            }
            updateConnectionQuality(null, false);
            return { 
              connected: false, 
              url: baseURL,
              error: 'Cannot reach server. Check:\n• Internet connection\n• Server is running\n• Server URL is correct'
            };
          }
        }
      }
    } catch (error) {
      // If we get ANY response (even 404/405/500), server is reachable
      if (error.response) {
        const latency = Date.now() - startTime;
        updateConnectionQuality(latency, true);
        return { connected: true, url: baseURL, latency: latency };
      }
      
      // Network error - server unreachable
      if (error.code === 'ERR_NETWORK' || error.code === 'ECONNABORTED' || !error.response) {
        if (attempt < maxAttempts) {
          await new Promise(resolve => setTimeout(resolve, 1000 * attempt));
          continue;
        }
        updateConnectionQuality(null, false);
        return { 
          connected: false, 
          url: baseURL,
          error: error.message || 'Network error. Please check your connection.'
        };
      }
      
      // Other errors - assume server is reachable
      const latency = Date.now() - startTime;
      updateConnectionQuality(latency, true);
      return { connected: true, url: baseURL, latency: latency };
    }
  }
  
  // Only mark as failed if all attempts truly failed
  updateConnectionQuality(null, false);
  return { 
    connected: false, 
    url: baseURL,
    error: 'Connection failed after multiple attempts'
  };
};

// Separate connection monitoring tunnel - runs in background
const startConnectionMonitor = () => {
  if (isMonitoring) return; // Already monitoring
  
  isMonitoring = true;
  
  // Initial check
  quickConnectionTest(5000).catch(() => {
    // Silent fail for background monitoring
  });
  
  // Set up interval for continuous monitoring
  connectionMonitorInterval = setInterval(async () => {
    try {
      await quickConnectionTest(5000);
    } catch (error) {
      // Silent fail - just update state
      console.log('Background connection check failed');
    }
  }, MONITOR_INTERVAL);
};

// Stop connection monitoring
const stopConnectionMonitor = () => {
  if (connectionMonitorInterval) {
    clearInterval(connectionMonitorInterval);
    connectionMonitorInterval = null;
  }
  isMonitoring = false;
};

// Get connection state
export const getConnectionState = () => ({ ...connectionState });

// Check connection state - be optimistic
export const isConnected = () => {
  // If we had a recent success, consider online even if current check failed
  if (connectionState.lastSuccess && (Date.now() - connectionState.lastSuccess) < 30000) {
    return true; // Consider online if we had success in last 30 seconds
  }
  return connectionState.isOnline;
};

// Start connection monitoring tunnel
export const startMonitoring = () => {
  startConnectionMonitor();
};

// Stop connection monitoring
export const stopMonitoring = () => {
  stopConnectionMonitor();
};

export const authService = {
  async checkConnection() {
    const result = await testConnection();
    return result.connected;
  },
  
  async checkConnectionDetailed() {
    return await testConnection();
  },
  
  getConnectionState() {
    return getConnectionState();
  },
  
  // Start background monitoring
  startMonitoring() {
    startConnectionMonitor();
  },
  
  // Stop background monitoring
  stopMonitoring() {
    stopConnectionMonitor();
  },

  async login(username, pin, retries = 3) {
    let lastError;
    
    // Validate input before making request
    if (!username || !username.trim()) {
      throw 'Username is required';
    }
    
    if (!pin || !pin.trim()) {
      throw 'PIN is required';
    }
    
    if (!/^\d{4}$/.test(pin)) {
      throw 'PIN must be exactly 4 digits';
    }
    
    // Try connection check first if offline, but don't block
    if (connectionState.quality === 'offline') {
      try {
        await testConnection(2); // Quick check with 2 attempts
      } catch (error) {
        // Continue anyway - login might work
        console.log('Connection check failed, proceeding with login');
      }
    }
    
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const response = await api.post(API_ENDPOINTS.LOGIN, {
          username: username.trim(),
          pin: pin.trim(),
        });
        
        // Check if response indicates success
        if (response.data && response.data.success === true) {
          // Store member data
          if (response.data.member) {
            await AsyncStorage.setItem('memberData', JSON.stringify(response.data.member));
          }
          // Store session info if provided
          if (response.data.session_id) {
            await AsyncStorage.setItem('sessionId', response.data.session_id);
          }
          // Store username and PIN for automatic login
          await AsyncStorage.setItem('storedUsername', username.trim());
          await AsyncStorage.setItem('storedPin', pin.trim());
          return response.data;
        }
        
        // If login failed but we got a response, extract the error message
        const errorMsg = response.data?.error || response.data?.message || 'Login failed';
        throw errorMsg;
      } catch (error) {
        lastError = error;
        
        // Handle specific error status codes
        if (error.response?.status === 400) {
          // Bad request - validation error
          throw error.response?.data?.error || 'Invalid input. Please check your username and PIN.';
        }
        
        if (error.response?.status === 401) {
          // Unauthorized - invalid credentials
          throw error.response?.data?.error || 'Invalid username or PIN. Please try again.';
        }
        
        if (error.response?.status === 403) {
          // Forbidden - account inactive
          throw error.response?.data?.error || 'Your account is inactive. Please contact administrator.';
        }
        
        if (error.response?.status === 404) {
          // Not found - member doesn't exist
          throw error.response?.data?.error || 'User not found. Please check your username.';
        }
        
        if (error.response?.status === 500) {
          // Server error
          throw error.response?.data?.error || 'Server error. Please try again later.';
        }
        
        // Don't retry on client errors (4xx)
        if (error.response?.status >= 400 && error.response?.status < 500) {
          throw error.response?.data?.error || error.message || 'Login failed';
        }
        
        // Retry on network errors or server errors (5xx)
        if (attempt < retries && (error.code === 'ERR_NETWORK' || error.code === 'ECONNABORTED' || (error.response?.status >= 500))) {
          // Try to reconnect before retry
          if (error.code === 'ERR_NETWORK' || error.code === 'ECONNABORTED') {
            try {
              await testConnection(1); // Quick connection test
            } catch (reconnectError) {
              // Continue with retry anyway
            }
          }
          
          // Wait a bit before retrying (exponential backoff with jitter)
          const backoffDelay = 1000 * Math.pow(2, attempt) + Math.random() * 500;
          await new Promise(resolve => setTimeout(resolve, backoffDelay));
          continue;
        }
        
        // Format error message
        if (error.response?.data?.error) {
          throw error.response.data.error;
        }
        
        if (error.code === 'ERR_NETWORK' || !error.response) {
          const connectionTest = await testConnection(1);
          if (!connectionTest.connected) {
            throw `Cannot connect to server at ${getBaseURL()}\n\nPlease check:\n• Your internet connection\n• Server is running\n• Server URL is correct\n• Both devices are on same network (if using local IP)`;
          }
          throw 'Network error occurred. Please try again.';
        }
        
        throw error.message || 'Login failed. Please try again.';
      }
    }
    
    throw lastError?.message || 'Login failed after multiple attempts';
  },

  async logout() {
    await AsyncStorage.removeItem('memberData');
    await AsyncStorage.removeItem('sessionId');
    await AsyncStorage.removeItem('storedUsername');
    await AsyncStorage.removeItem('storedPin');
  },

  async getStoredMember() {
    const memberData = await AsyncStorage.getItem('memberData');
    return memberData ? JSON.parse(memberData) : null;
  },

  async getStoredCredentials() {
    const username = await AsyncStorage.getItem('storedUsername');
    const pin = await AsyncStorage.getItem('storedPin');
    if (username && pin) {
      return { username, pin };
    }
    return null;
  },

  async autoLogin() {
    try {
      const credentials = await this.getStoredCredentials();
      if (!credentials) {
        return { success: false, error: 'No stored credentials' };
      }
      
      // Attempt login with stored credentials
      const result = await this.login(credentials.username, credentials.pin, 2);
      return result;
    } catch (error) {
      return { 
        success: false, 
        error: typeof error === 'string' ? error : (error.message || 'Auto-login failed') 
      };
    }
  },
};

export const accountService = {
  async getAccountInfo() {
    return retryRequest(async () => {
      const response = await api.get(API_ENDPOINTS.ACCOUNT_INFO);
      return response.data;
    }).catch(error => {
      throw error.response?.data?.error || error.message || 'Failed to fetch account info';
    });
  },

  async getAccountSummary(year = null, month = null) {
    return retryRequest(async () => {
      const params = {};
      if (year) params.year = year;
      if (month) params.month = month;
      const response = await api.get(API_ENDPOINTS.ACCOUNT_SUMMARY, { params });
      return response.data;
    }).catch(error => {
      throw error.response?.data?.error || error.message || 'Failed to fetch account summary';
    });
  },

  async getTransactionHistory(page = 1, limit = 20) {
    return retryRequest(async () => {
      const response = await api.get(API_ENDPOINTS.TRANSACTIONS, {
        params: { page, limit },
      });
      return response.data;
    }).catch(error => {
      throw error.response?.data?.error || error.message || 'Failed to fetch transactions';
    });
  },

  async getBalanceTransactions(page = 1, limit = 20) {
    return retryRequest(async () => {
      const response = await api.get(API_ENDPOINTS.BALANCE_TRANSACTIONS, {
        params: { page, limit },
      });
      return response.data;
    }).catch(error => {
      throw error.response?.data?.error || error.message || 'Failed to fetch balance transactions';
    });
  },
};

export const fundTransferService = {
  async searchMember(rfid) {
    return retryRequest(async () => {
      const response = await api.get(API_ENDPOINTS.SEARCH_MEMBER, {
        params: { rfid: rfid.trim() },
      });
      return response.data;
    }).catch(error => {
      throw error.response?.data?.error || error.message || 'Failed to search member';
    });
  },

  async requestTransferOTP(recipientRfid, amount, notes = '') {
    return retryRequest(async () => {
      const response = await api.post(API_ENDPOINTS.REQUEST_TRANSFER_OTP, {
        recipient_rfid: recipientRfid.trim(),
        amount: parseFloat(amount),
        notes: notes.trim(),
      });
      return response.data;
    }).catch(error => {
      throw error.response?.data?.error || error.message || 'Failed to request OTP';
    });
  },

  async verifyTransferOTP(otpCode) {
    return retryRequest(async () => {
      const response = await api.post(API_ENDPOINTS.VERIFY_TRANSFER_OTP, {
        otp_code: otpCode.trim(),
      });
      return response.data;
    }).catch(error => {
      throw error.response?.data?.error || error.message || 'Failed to verify OTP';
    });
  },
};

export default api;

