import React, { useState, useEffect, useRef } from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { StatusBar } from 'expo-status-bar';
import { Ionicons } from '@expo/vector-icons';
import { authService, accountService } from './services/api';

import LoginScreen from './screens/LoginScreen';
import HomeScreen from './screens/HomeScreen';
import TransactionsScreen from './screens/TransactionsScreen';
import FundTransferScreen from './screens/FundTransferScreen';

const Stack = createNativeStackNavigator();
const Tab = createBottomTabNavigator();

function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        tabBarIcon: ({ focused, color, size }) => {
          let iconName;

          if (route.name === 'Home') {
            iconName = focused ? 'home' : 'home-outline';
          } else if (route.name === 'FundTransfer') {
            iconName = focused ? 'swap-horizontal' : 'swap-horizontal-outline';
          } else if (route.name === 'Transactions') {
            iconName = focused ? 'receipt' : 'receipt-outline';
          }

          return <Ionicons name={iconName} size={size} color={color} />;
        },
        tabBarActiveTintColor: '#1f7a3a',
        tabBarInactiveTintColor: '#94a3b8',
        headerShown: false,
      })}
    >
      <Tab.Screen
        name="Home"
        component={HomeScreen}
        options={{
          tabBarLabel: 'Home',
        }}
      />
      <Tab.Screen
        name="FundTransfer"
        component={FundTransferScreen}
        options={{
          tabBarLabel: 'Fund Transfer',
        }}
      />
      <Tab.Screen
        name="Transactions"
        component={TransactionsScreen}
        options={{
          tabBarLabel: 'Transactions',
        }}
      />
    </Tab.Navigator>
  );
}

export default function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const navigationRef = useRef(null);

  useEffect(() => {
    checkAuth();
  }, []);

  const checkAuth = async () => {
    try {
      const member = await authService.getStoredMember();
      if (member) {
        // Validate session by trying to get account info
        try {
          const accountResponse = await accountService.getAccountInfo();
          if (accountResponse && accountResponse.success) {
            setIsAuthenticated(true);
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
      if (credentials) {
        try {
          console.log('Attempting automatic login with stored PIN...');
          const autoLoginResult = await authService.autoLogin();
          if (autoLoginResult && autoLoginResult.success) {
            // Auto-login successful
            setIsAuthenticated(true);
            return;
          } else {
            console.log('Auto-login failed:', autoLoginResult?.error || 'Unknown error');
            // Clear invalid credentials
            await authService.logout();
            setIsAuthenticated(false);
          }
        } catch (error) {
          console.log('Auto-login error:', error);
          // Clear invalid credentials on error
          await authService.logout();
          setIsAuthenticated(false);
        }
      } else {
        setIsAuthenticated(false);
      }
    } catch (error) {
      // Clear any invalid stored data
      await authService.logout();
      setIsAuthenticated(false);
    } finally {
      setIsLoading(false);
    }
  };

  if (isLoading) {
    return null; // Or a loading screen
  }

  return (
    <NavigationContainer ref={navigationRef}>
      <StatusBar style="auto" />
      <Stack.Navigator 
        screenOptions={{ headerShown: false }}
        initialRouteName={isAuthenticated ? "Main" : "Login"}
      >
        <Stack.Screen name="Login" component={LoginScreen} />
        <Stack.Screen name="Main" component={MainTabs} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}

