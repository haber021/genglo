import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
  Alert,
  Modal,
  FlatList,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { accountService, authService } from '../services/api';
import { colors } from '../constants/colors';

export default function HomeScreen({ navigation }) {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [selectedYear, setSelectedYear] = useState(new Date().getFullYear());
  const [selectedMonth, setSelectedMonth] = useState(new Date().getMonth() + 1);
  const [showMonthPicker, setShowMonthPicker] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  useEffect(() => {
    loadAccountSummary(selectedYear, selectedMonth);
  }, [selectedYear, selectedMonth]);

  const loadAccountSummary = async (year, month) => {
    try {
      const response = await accountService.getAccountSummary(year, month);
      if (response.success) {
        setSummary(response.summary);
        // Update selected month/year from response if provided
        if (response.summary.selected_year) {
          setSelectedYear(response.summary.selected_year);
        }
        if (response.summary.selected_month) {
          setSelectedMonth(response.summary.selected_month);
        }
      }
    } catch (error) {
      // Check if it's an authentication error
      const errorMessage = typeof error === 'string' ? error : error.message || '';
      if (errorMessage.includes('Authentication') || errorMessage.includes('401') || errorMessage.includes('Unauthorized')) {
        // Session expired, logout and redirect to login
        await authService.logout();
        navigation.replace('Login');
      } else {
        Alert.alert('Error', error.toString());
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  const handleRefresh = () => {
    setRefreshing(true);
    loadAccountSummary(selectedYear, selectedMonth);
  };

  const getMonthName = (month) => {
    const months = [
      'January', 'February', 'March', 'April', 'May', 'June',
      'July', 'August', 'September', 'October', 'November', 'December'
    ];
    return months[month - 1] || months[0];
  };

  const handleMonthSelect = (year, month) => {
    setSelectedYear(year);
    setSelectedMonth(month);
    setShowMonthPicker(false);
    setLoading(true);
    loadAccountSummary(year, month);
  };

  const generateMonthOptions = () => {
    const options = [];
    const currentDate = new Date();
    const currentYear = currentDate.getFullYear();
    const currentMonth = currentDate.getMonth() + 1;
    
    // Generate options for last 12 months
    for (let i = 0; i < 12; i++) {
      let year = currentYear;
      let month = currentMonth - i;
      
      if (month <= 0) {
        month += 12;
        year -= 1;
      }
      
      options.push({ year, month, label: `${getMonthName(month)} ${year}` });
    }
    
    return options;
  };

  const handleLogout = async () => {
    setShowSettings(false);
    Alert.alert(
      'Logout',
      'Are you sure you want to logout?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Logout',
          style: 'destructive',
          onPress: async () => {
            await authService.logout();
            navigation.replace('Login');
          },
        },
      ]
    );
  };

  const formatCurrency = (amount) => {
    const num = parseFloat(amount || 0);
    return `₱${num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  const formatDate = (dateString) => {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  };

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.brand} />
      </View>
    );
  }

  if (!summary) {
    return (
      <View style={styles.centered}>
        <Text>No data available</Text>
      </View>
    );
  }

  const { member, recent_transactions, recent_balance_transactions, total_spent_this_month } = summary;

  return (
    <ScrollView
      style={styles.container}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={handleRefresh} />
      }
    >
      <View style={styles.header}>
        <View>
          <Text style={styles.greeting}>Welcome back,</Text>
          <Text style={styles.name}>{member.full_name}</Text>
        </View>
        <TouchableOpacity onPress={() => setShowSettings(true)} style={styles.settingsButton}>
          <Ionicons name="settings-outline" size={24} color={colors.textWhite} />
        </TouchableOpacity>
      </View>

      {/* Account Balance Cards */}
      <View style={styles.balanceSection}>
        <View style={styles.balanceCard}>
          <Text style={styles.balanceLabel}>Account Balance</Text>
          <Text style={styles.balanceAmount}>{formatCurrency(member.balance)}</Text>
        </View>

      </View>

      {/* Monthly Summary */}
      <View style={styles.monthlySection}>
        <View style={styles.monthlyHeader}>
          <View style={styles.monthlyHeaderLeft}>
            <Ionicons name="calendar-outline" size={24} color={colors.brand} />
            <Text style={styles.monthlyTitle}>
              {selectedYear === new Date().getFullYear() && selectedMonth === new Date().getMonth() + 1
                ? 'This Month'
                : `${getMonthName(selectedMonth)} ${selectedYear}`}
            </Text>
          </View>
          <TouchableOpacity
            onPress={() => setShowMonthPicker(true)}
            style={styles.monthPickerButton}
          >
            <Ionicons name="chevron-down" size={18} color={colors.brand} />
          </TouchableOpacity>
        </View>

        <View style={styles.metricsGrid}>
          <View style={styles.metricCard}>
            <View style={[styles.metricIconContainer, { backgroundColor: '#e8f5e9' }]}>
              <Ionicons name="cash-outline" size={24} color={colors.brand} />
            </View>
            <Text style={styles.metricLabel}>Total Spent</Text>
            <Text style={styles.metricValue}>{formatCurrency(total_spent_this_month)}</Text>
          </View>

        </View>
      </View>

      {/* Quick Actions */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Quick Actions</Text>
        <TouchableOpacity
          style={styles.actionButton}
          onPress={() => navigation.navigate('Transactions')}
        >
          <Text style={styles.actionButtonText}>View Transaction History</Text>
        </TouchableOpacity>
      </View>

      {/* Recent Transactions */}
      {recent_transactions && recent_transactions.length > 0 && (
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Recent Transactions</Text>
            <TouchableOpacity onPress={() => navigation.navigate('Transactions')}>
              <Text style={styles.seeAllText}>See All</Text>
            </TouchableOpacity>
          </View>
          {recent_transactions.slice(0, 5).map((transaction) => (
            <View key={transaction.id} style={styles.transactionItem}>
              <View style={styles.transactionInfo}>
                <Text style={styles.transactionNumber}>{transaction.transaction_number}</Text>
                <Text style={styles.transactionDate}>{formatDate(transaction.created_at)}</Text>
              </View>
              <Text style={styles.transactionAmount}>
                {formatCurrency(transaction.total_amount)}
              </Text>
            </View>
          ))}
        </View>
      )}

      {/* Month Picker Modal */}
      <Modal
        visible={showMonthPicker}
        transparent={true}
        animationType="slide"
        onRequestClose={() => setShowMonthPicker(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Select Month</Text>
              <TouchableOpacity
                onPress={() => setShowMonthPicker(false)}
                style={styles.closeButton}
              >
                <Text style={styles.closeButtonText}>✕</Text>
              </TouchableOpacity>
            </View>
            <FlatList
              data={generateMonthOptions()}
              keyExtractor={(item) => `${item.year}-${item.month}`}
              renderItem={({ item }) => (
                <TouchableOpacity
                  style={[
                    styles.monthOption,
                    selectedYear === item.year && selectedMonth === item.month && styles.monthOptionSelected
                  ]}
                  onPress={() => handleMonthSelect(item.year, item.month)}
                >
                  <Text
                    style={[
                      styles.monthOptionText,
                      selectedYear === item.year && selectedMonth === item.month && styles.monthOptionTextSelected
                    ]}
                  >
                    {item.label}
                  </Text>
                  {selectedYear === item.year && selectedMonth === item.month && (
                    <Text style={styles.checkmark}>✓</Text>
                  )}
                </TouchableOpacity>
              )}
            />
          </View>
        </View>
      </Modal>

      {/* Settings Modal */}
      <Modal
        visible={showSettings}
        transparent={true}
        animationType="fade"
        onRequestClose={() => setShowSettings(false)}
      >
        <TouchableOpacity
          style={styles.settingsOverlay}
          activeOpacity={1}
          onPress={() => setShowSettings(false)}
        >
          <View style={styles.settingsContent} onStartShouldSetResponder={() => true}>
            <View style={styles.settingsHeader}>
              <Text style={styles.settingsTitle}>Settings</Text>
              <TouchableOpacity
                onPress={() => setShowSettings(false)}
                style={styles.settingsCloseButton}
              >
                <Ionicons name="close" size={24} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            <TouchableOpacity
              style={styles.settingsOption}
              onPress={handleLogout}
            >
              <View style={styles.settingsOptionLeft}>
                <Ionicons name="log-out-outline" size={24} color={colors.error} />
                <Text style={styles.settingsOptionText}>Logout</Text>
              </View>
              <Ionicons name="chevron-forward" size={20} color={colors.textSecondary} />
            </TouchableOpacity>
          </View>
        </TouchableOpacity>
      </Modal>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  centered: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: colors.background,
  },
  header: {
    backgroundColor: colors.brand,
    padding: 20,
    paddingTop: 60,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  greeting: {
    color: colors.textWhite,
    fontSize: 16,
    opacity: 0.9,
  },
  name: {
    color: colors.textWhite,
    fontSize: 24,
    fontWeight: 'bold',
    marginTop: 4,
  },
  settingsButton: {
    padding: 8,
  },
  balanceSection: {
    flexDirection: 'row',
    padding: 15,
    gap: 15,
  },
  balanceCard: {
    flex: 1,
    backgroundColor: colors.panel,
    borderRadius: 12,
    padding: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  balanceLabel: {
    fontSize: 14,
    color: colors.textSecondary,
    marginBottom: 8,
  },
  balanceAmount: {
    fontSize: 28,
    fontWeight: 'bold',
    color: colors.brand,
  },
  section: {
    backgroundColor: colors.panel,
    margin: 15,
    marginTop: 0,
    borderRadius: 12,
    padding: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 15,
  },
  sectionTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    color: colors.textPrimary,
  },
  seeAllText: {
    color: colors.brand,
    fontSize: 16,
  },
  monthlySection: {
    margin: 15,
    marginTop: 0,
  },
  monthlyHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  monthlyHeaderLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  monthlyTitle: {
    fontSize: 22,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  metricsGrid: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 12,
  },
  metricCard: {
    flex: 1,
    backgroundColor: colors.panel,
    borderRadius: 16,
    padding: 18,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.08,
    shadowRadius: 8,
    elevation: 3,
  },
  metricIconContainer: {
    width: 48,
    height: 48,
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
  },
  metricLabel: {
    fontSize: 12,
    color: colors.textSecondary,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 6,
  },
  metricValue: {
    fontSize: 20,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  actionButton: {
    backgroundColor: colors.brand,
    borderRadius: 8,
    padding: 15,
    alignItems: 'center',
    marginBottom: 10,
  },
  actionButtonText: {
    color: colors.textWhite,
    fontSize: 16,
    fontWeight: '600',
  },
  transactionItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  transactionInfo: {
    flex: 1,
  },
  transactionNumber: {
    fontSize: 16,
    fontWeight: '600',
    color: colors.textPrimary,
    marginBottom: 4,
  },
  transactionDate: {
    fontSize: 14,
    color: colors.textSecondary,
  },
  transactionAmount: {
    fontSize: 18,
    fontWeight: 'bold',
    color: colors.brand,
  },
  monthPickerButton: {
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 6,
    backgroundColor: colors.borderLight,
  },
  monthPickerText: {
    color: colors.brand,
    fontSize: 14,
    fontWeight: '600',
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: colors.panel,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    maxHeight: '70%',
    paddingBottom: 20,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 20,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  modalTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    color: colors.textPrimary,
  },
  closeButton: {
    padding: 5,
  },
  closeButtonText: {
    fontSize: 24,
    color: colors.textSecondary,
  },
  monthOption: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 15,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  monthOptionSelected: {
    backgroundColor: '#e8f5e9', // Light green tint for selected month
  },
  monthOptionText: {
    fontSize: 16,
    color: colors.textPrimary,
  },
  monthOptionTextSelected: {
    color: colors.brand,
    fontWeight: '600',
  },
  checkmark: {
    fontSize: 18,
    color: colors.brand,
    fontWeight: 'bold',
  },
  settingsOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  settingsContent: {
    backgroundColor: colors.panel,
    borderRadius: 16,
    width: '100%',
    maxWidth: 400,
    overflow: 'hidden',
  },
  settingsHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 20,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  settingsTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    color: colors.textPrimary,
  },
  settingsCloseButton: {
    padding: 4,
  },
  settingsOption: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 20,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  settingsOptionLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  settingsOptionText: {
    fontSize: 16,
    color: colors.textPrimary,
    fontWeight: '500',
  },
});

