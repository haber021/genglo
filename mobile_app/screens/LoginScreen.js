import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Alert,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
} from 'react-native';
import { authService, accountService, getConnectionState, startMonitoring } from '../services/api';
import { API_BASE_URL, isApiUrlValid } from '../config';
import { colors } from '../constants/colors';

export default function LoginScreen({ navigation }) {
  const [username, setUsername] = useState('');
  const [pin, setPin] = useState('');
  const [loading, setLoading] = useState(false);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [checkingConnection, setCheckingConnection] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState(null);
  const [connectionQuality, setConnectionQuality] = useState(null);
  const [connectionLatency, setConnectionLatency] = useState(null);
  const [isAutoLoggingIn, setIsAutoLoggingIn] = useState(false);
  const pinInputRef = useRef(null);
  const pinValueRef = useRef('');

  useEffect(() => {
    // Load stored username if available
    const loadStoredUsername = async () => {
      try {
        const credentials = await authService.getStoredCredentials();
        if (credentials && credentials.username) {
          setUsername(credentials.username);
        }
      } catch (error) {
        console.log('Could not load stored username');
      }
    };
    
    loadStoredUsername();
    
    // Check if user is already logged in
    checkAuth();
    
    // Start background connection monitoring tunnel
    startMonitoring();
    
    // Auto-check connection on mount
    autoCheckConnection();
    
    // Check connection state periodically (for UI updates)
    const connectionInterval = setInterval(() => {
      const state = getConnectionState();
      setConnectionQuality(state.quality);
      setConnectionLatency(state.latency);
      
      // Auto-retry connection if offline (but monitoring tunnel handles this too)
      if (state.quality === 'offline' && !checkingConnection) {
        autoCheckConnection();
      }
    }, 3000); // Check UI state every 3 seconds
    
    return () => {
      clearInterval(connectionInterval);
      // Note: We don't stop monitoring here as it should run in background
    };
  }, []);

  // Auto-check connection silently in background
  const autoCheckConnection = async () => {
    if (checkingConnection) return;
    
    try {
      const result = await authService.checkConnectionDetailed();
      if (result.connected) {
        setConnectionStatus('connected');
        setConnectionQuality(result.quality || 'good');
        setConnectionLatency(result.latency);
      } else {
        // Don't immediately mark as failed - might be temporary
        // Only update if we're sure
        const state = getConnectionState();
        if (state.consecutiveFailures >= 3) {
          setConnectionStatus('failed');
          setConnectionQuality('offline');
        }
      }
    } catch (error) {
      // Don't immediately mark as offline on error
      // Let the monitoring tunnel handle it
      const state = getConnectionState();
      setConnectionQuality(state.quality || 'good');
    }
  };

  const checkAuth = async () => {
    try {
      const member = await authService.getStoredMember();
      if (member) {
        // Validate session by trying to get account info
        try {
          const accountResponse = await accountService.getAccountInfo();
          if (accountResponse && accountResponse.success) {
            // Session is valid, navigate to main tabs
            navigation.replace('Main');
            return;
          } else {
            // Invalid response, try auto-login with stored PIN
            console.log('Session expired, attempting auto-login with stored PIN');
          }
        } catch (error) {
          // Session invalid, try auto-login with stored PIN
          console.log('Session expired, attempting auto-login with stored PIN');
        }
      }
      
      // If session is invalid or no member stored, try auto-login with stored credentials
      const credentials = await authService.getStoredCredentials();
      if (credentials && credentials.username && credentials.pin) {
        // Validate stored PIN format before attempting auto-login
        if (credentials.pin.length === 4 && /^\d{4}$/.test(credentials.pin)) {
          setIsAutoLoggingIn(true);
          try {
            console.log('Attempting automatic login with stored PIN...');
            const autoLoginResult = await authService.autoLogin();
            if (autoLoginResult && autoLoginResult.success) {
              // Auto-login successful, navigate to main tabs
              setIsAutoLoggingIn(false);
              navigation.replace('Main');
              return;
            } else {
              console.log('Auto-login failed:', autoLoginResult?.error || 'Unknown error');
              // Clear invalid credentials silently (don't show error to user)
              // Only clear if it's an authentication error, not a network error
              if (autoLoginResult?.error && !autoLoginResult.error.includes('connect') && !autoLoginResult.error.includes('network')) {
                await authService.logout();
              }
            }
          } catch (error) {
            console.log('Auto-login error:', error);
            // Only clear credentials if it's an authentication error
            const errorMsg = typeof error === 'string' ? error : (error.message || '');
            if (!errorMsg.includes('connect') && !errorMsg.includes('network') && !errorMsg.includes('timeout')) {
              await authService.logout();
            }
          } finally {
            setIsAutoLoggingIn(false);
          }
        } else {
          // Stored PIN is invalid format, clear it
          console.log('Stored PIN has invalid format, clearing credentials');
          await authService.logout();
        }
      }
    } catch (error) {
      console.log('No stored auth');
    } finally {
      setCheckingAuth(false);
    }
  };

  const testConnection = async () => {
    setCheckingConnection(true);
    setConnectionStatus(null);
    try {
      // Validate URL first
      if (!isApiUrlValid()) {
        setConnectionStatus('failed');
        Alert.alert(
          'Invalid Server URL',
          `The server URL is invalid: ${API_BASE_URL}\n\nPlease check your config.js file and ensure the URL is properly formatted (e.g., http://192.168.1.100:8000)`
        );
        return;
      }
      
      const result = await authService.checkConnectionDetailed();
      if (result.connected) {
        setConnectionStatus('connected');
        setConnectionQuality(result.quality || 'good');
        setConnectionLatency(result.latency);
        
        const qualityText = result.quality === 'excellent' ? 'Excellent' :
                           result.quality === 'good' ? 'Good' :
                           result.quality === 'poor' ? 'Poor' : 'Unknown';
        const latencyText = result.latency ? `\nLatency: ${result.latency}ms` : '';
        
        Alert.alert(
          'Connection Success', 
          `✓ Connected to server\n\nURL: ${result.url}\nQuality: ${qualityText}${latencyText}\n\nYou can now login.`
        );
      } else {
        setConnectionStatus('failed');
        setConnectionQuality('offline');
        Alert.alert(
          'Connection Failed',
          result.error || `Cannot reach server at ${result.url}\n\nPlease check:\n• Your internet connection\n• Server URL in config.js\n• Server is running\n• Both devices on same network (if using local IP)`
        );
      }
    } catch (error) {
      setConnectionStatus('failed');
      setConnectionQuality('offline');
      Alert.alert(
        'Connection Error',
        `Error: ${error.message || 'Unknown error'}\n\nServer URL: ${API_BASE_URL}\n\nTroubleshooting:\n1. Check if Django server is running\n2. Verify server URL in config.js\n3. Ensure both devices are on same network\n4. Check firewall settings`
      );
    } finally {
      setCheckingConnection(false);
    }
  };

  const handleLogin = async (pinToUse = null) => {
    // Don't allow manual login while auto-login is in progress
    if (isAutoLoggingIn) {
      return;
    }

    // Use provided PIN or current PIN state, with fallback to ref
    const currentPin = pinToUse || pin || pinValueRef.current;
    const currentUsername = username.trim();

    if (!currentUsername || !currentPin || !currentPin.trim()) {
      Alert.alert('Missing Information', 'Please enter both username and PIN');
      return;
    }

    // Validate PIN format - only show error if PIN is clearly invalid
    // Since we filter to numeric only and limit to 4 chars, PIN should always be valid format
    // Only check if PIN is incomplete (less than 4 digits) when user tries to submit manually
    const trimmedPin = currentPin.trim();
    if (trimmedPin.length < 4) {
      // PIN is incomplete - don't show error, just don't proceed
      // User might still be typing, so silently return
      return;
    }

    // PIN is 4 digits and numeric (guaranteed by input filtering)
    // Let server handle authentication - don't show format errors for valid PINs
    setLoading(true);
    
    // Auto-check connection first, but don't block login if it fails
    // Sometimes connection works even if check fails
    if (connectionQuality === 'offline' || !connectionQuality) {
      try {
        await autoCheckConnection();
        // Small delay to let connection state update
        await new Promise(resolve => setTimeout(resolve, 500));
      } catch (error) {
        // Continue anyway - login might still work
        console.log('Connection check failed, trying login anyway');
      }
    }

    try {
      // Try login with increased retries for better reliability
      // Use currentPin which might be from parameter, state, or ref
      const pinForLogin = pinToUse || pin || pinValueRef.current;
      const result = await authService.login(currentUsername, pinForLogin, 3);
      if (result && result.success) {
        // Update connection status on successful login
        setConnectionStatus('connected');
        setConnectionQuality('good');
        // Navigate immediately on success
        navigation.replace('Main');
        return; // Exit early on success
      } else {
        // Handle case where result doesn't have success flag
        const errorMsg = result?.error || 'Login failed. Please try again.';
        // Replace format validation errors with generic authentication error
        // (We already validated PIN format client-side, so format errors shouldn't appear)
        const displayError = (errorMsg.toLowerCase().includes('pin must be') || 
                              errorMsg.toLowerCase().includes('invalid pin') ||
                              errorMsg.toLowerCase().includes('pin must be exactly'))
          ? 'Invalid username or PIN. Please check your credentials.'
          : errorMsg;
        
        Alert.alert(
          'Login Failed',
          displayError,
          [{ text: 'OK', style: 'cancel' }]
        );
      }
    } catch (error) {
      // Show user-friendly error message
      const errorMessage = typeof error === 'string' ? error : (error.message || 'Login failed. Please try again.');
      
      // Replace "Invalid PIN" format errors with generic authentication error
      // (We already validated PIN format client-side, so format errors shouldn't appear)
      let displayErrorMessage = errorMessage;
      if (errorMessage.toLowerCase().includes('pin must be') || 
          errorMessage.toLowerCase().includes('invalid pin') ||
          errorMessage.toLowerCase().includes('pin must be exactly')) {
        displayErrorMessage = 'Invalid username or PIN. Please check your credentials.';
      }
      
      // If it's a network error, try to reconnect and retry once
      if (errorMessage.includes('connect') || errorMessage.includes('network') || errorMessage.includes('timeout')) {
        // Try to reconnect
        try {
          await autoCheckConnection();
          await new Promise(resolve => setTimeout(resolve, 1000));
          
          // Retry login once more
          try {
            const pinForRetry = pinToUse || pin || pinValueRef.current;
            const retryResult = await authService.login(currentUsername, pinForRetry, 2);
            if (retryResult && retryResult.success) {
              setConnectionStatus('connected');
              setConnectionQuality('good');
              navigation.replace('Main');
              return;
            }
          } catch (retryError) {
            // Retry also failed, show error
          }
        } catch (reconnectError) {
          // Reconnection failed, continue to show error
        }
      }
      
      Alert.alert(
        'Login Failed',
        displayErrorMessage,
        [
          {
            text: 'Retry',
            onPress: handleLogin,
            style: 'default',
          },
          {
            text: 'OK',
            style: 'cancel',
          },
        ]
      );
    } finally {
      setLoading(false);
    }
  };

  if (checkingAuth || isAutoLoggingIn) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.brand} />
        {isAutoLoggingIn && (
          <Text style={styles.autoLoginText}>Logging in automatically...</Text>
        )}
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.content}>
          <Text style={styles.title}>Genglo Printing Services</Text>
          <Text style={styles.subtitle}>Member Login</Text>

          {/* Server URL Display - Collapsible */}
          <View style={styles.serverInfo}>
            <Text style={styles.serverLabel}>Server:</Text>
            <Text style={styles.serverUrl} numberOfLines={1}>
              {API_BASE_URL}
            </Text>
            {connectionQuality && (
              <View style={styles.connectionStatus}>
                <View style={[
                  styles.statusIndicator,
                  connectionQuality === 'excellent' && styles.statusExcellent,
                  connectionQuality === 'good' && styles.statusGood,
                  connectionQuality === 'poor' && styles.statusPoor,
                  connectionQuality === 'offline' && styles.statusOffline,
                ]} />
                <Text style={styles.statusText}>
                  {connectionQuality === 'excellent' ? '✓ Connected' :
                   connectionQuality === 'good' ? '✓ Connected' :
                   connectionQuality === 'poor' ? '⚠ Slow Connection' :
                   '⚠ Offline - Will try to connect'}
                  {connectionLatency && connectionQuality !== 'offline' && ` (${connectionLatency}ms)`}
                </Text>
              </View>
            )}
            {connectionQuality === 'offline' && (
              <Text style={styles.offlineHint}>
                Don't worry! Login will automatically try to connect.
              </Text>
            )}
          </View>

          <View style={styles.form}>
            <Text style={styles.label}>Username</Text>
            <TextInput
              style={styles.input}
              placeholder="Enter username"
              value={username}
              onChangeText={setUsername}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="default"
              editable={!loading}
              placeholderTextColor={colors.textMuted}
              returnKeyType="next"
              onSubmitEditing={() => {
                pinInputRef.current?.focus();
              }}
            />

            <Text style={styles.label}>PIN</Text>
            <TextInput
              ref={pinInputRef}
              style={styles.input}
              placeholder="Enter 4-digit PIN"
              value={pin}
              onChangeText={(text) => {
                // Only allow numeric input
                const numericText = text.replace(/[^0-9]/g, '');
                setPin(numericText);
                pinValueRef.current = numericText; // Update ref immediately
                
                // Auto-submit when 4 digits are entered and valid
                // Only auto-submit if PIN is exactly 4 digits and username is provided
                if (numericText.length === 4 && /^\d{4}$/.test(numericText) && username.trim() && !loading && !isAutoLoggingIn) {
                  // Small delay to let user see the last digit before auto-submitting
                  setTimeout(() => {
                    // Use ref to get current PIN value (more reliable than state)
                    const currentPin = pinValueRef.current;
                    const currentUsername = username.trim();
                    // Re-check all conditions before submitting
                    if (currentPin.length === 4 && /^\d{4}$/.test(currentPin) && currentUsername && !loading && !isAutoLoggingIn) {
                      // Pass the PIN value directly to avoid state update timing issues
                      handleLogin(currentPin);
                    }
                  }, 300);
                }
              }}
              secureTextEntry
              keyboardType="numeric"
              maxLength={4}
              editable={!loading && !isAutoLoggingIn}
              placeholderTextColor={colors.textMuted}
              returnKeyType="go"
              onSubmitEditing={() => {
                // Only submit if PIN is valid (4 digits)
                if (pin.length === 4 && /^\d{4}$/.test(pin) && username.trim()) {
                  handleLogin();
                }
              }}
              blurOnSubmit={false}
            />

            <TouchableOpacity
              style={[styles.button, (loading || !username.trim() || !pin.trim() || isAutoLoggingIn) && styles.buttonDisabled]}
              onPress={handleLogin}
              disabled={loading || !username.trim() || !pin.trim() || isAutoLoggingIn}
            >
              {loading ? (
                <View style={styles.loadingContainer}>
                  <ActivityIndicator color={colors.textWhite} size="small" />
                  <Text style={styles.loadingText}>Connecting...</Text>
                </View>
              ) : (
                <Text style={styles.buttonText}>Login</Text>
              )}
            </TouchableOpacity>

            {connectionQuality === 'offline' && !loading && (
              <View style={styles.infoBox}>
                <Text style={styles.infoText}>
                  💡 Tip: Even if offline, login will automatically try to connect. Just tap Login!
                </Text>
              </View>
            )}
          </View>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  scrollContent: {
    flexGrow: 1,
  },
  centered: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: colors.background,
  },
  content: {
    flex: 1,
    justifyContent: 'center',
    padding: 20,
  },
  title: {
    fontSize: 32,
    fontWeight: 'bold',
    textAlign: 'center',
    marginBottom: 8,
    color: colors.textPrimary,
  },
  subtitle: {
    fontSize: 18,
    textAlign: 'center',
    marginBottom: 20,
    color: colors.textSecondary,
  },
  serverInfo: {
    backgroundColor: colors.panel,
    borderRadius: 8,
    padding: 12,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: colors.border,
  },
  serverLabel: {
    fontSize: 12,
    color: colors.textSecondary,
    marginBottom: 4,
  },
  serverUrl: {
    fontSize: 14,
    color: colors.textPrimary,
    fontWeight: '500',
    marginBottom: 8,
  },
  testButton: {
    alignSelf: 'flex-start',
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 6,
    backgroundColor: colors.borderLight,
  },
  testButtonText: {
    color: colors.brand,
    fontSize: 14,
    fontWeight: '600',
  },
  connectionStatus: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 8,
    marginBottom: 8,
  },
  statusIndicator: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 6,
  },
  statusExcellent: {
    backgroundColor: '#28a745',
  },
  statusGood: {
    backgroundColor: '#17a2b8',
  },
  statusPoor: {
    backgroundColor: '#ffc107',
  },
  statusOffline: {
    backgroundColor: '#dc3545',
  },
  statusText: {
    fontSize: 12,
    color: colors.textSecondary,
    fontWeight: '500',
  },
  form: {
    width: '100%',
  },
  label: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 8,
    color: colors.textPrimary,
  },
  input: {
    backgroundColor: colors.panel,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    padding: 15,
    fontSize: 16,
    marginBottom: 20,
    color: colors.textPrimary,
  },
  button: {
    backgroundColor: colors.brand,
    borderRadius: 8,
    padding: 15,
    alignItems: 'center',
    marginTop: 10,
  },
  buttonDisabled: {
    opacity: 0.6,
  },
  buttonText: {
    color: colors.textWhite,
    fontSize: 18,
    fontWeight: '600',
  },
  errorBox: {
    backgroundColor: '#fff3cd',
    borderWidth: 1,
    borderColor: colors.warning,
    borderRadius: 8,
    padding: 12,
    marginTop: 15,
  },
  errorText: {
    color: '#856404',
    fontSize: 14,
    textAlign: 'center',
  },
  infoBox: {
    backgroundColor: '#e7f3ff',
    borderWidth: 1,
    borderColor: '#b3d9ff',
    borderRadius: 8,
    padding: 12,
    marginTop: 15,
  },
  infoText: {
    color: '#004085',
    fontSize: 13,
    textAlign: 'center',
  },
  offlineHint: {
    fontSize: 12,
    color: colors.textSecondary,
    fontStyle: 'italic',
    marginTop: 4,
  },
  loadingContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  loadingText: {
    color: colors.textWhite,
    marginLeft: 8,
    fontSize: 16,
    fontWeight: '600',
  },
  autoLoginText: {
    marginTop: 16,
    fontSize: 16,
    color: colors.textSecondary,
    textAlign: 'center',
  },
});

