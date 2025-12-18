import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  ActivityIndicator,
  RefreshControl,
  Alert,
  TouchableOpacity,
  Modal,
  TouchableWithoutFeedback,
} from 'react-native';
import { accountService } from '../services/api';
import { colors } from '../constants/colors';

export default function TransactionsScreen() {
  const [transactions, setTransactions] = useState([]);
  const [balanceTransactions, setBalanceTransactions] = useState([]);
  const [allTransactions, setAllTransactions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [page, setPage] = useState(1);
  const [balancePage, setBalancePage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [hasMoreBalance, setHasMoreBalance] = useState(true);
  const [pagination, setPagination] = useState(null);
  const [balancePagination, setBalancePagination] = useState(null);
  const [showAll, setShowAll] = useState(false);
  const [dropdownVisible, setDropdownVisible] = useState(false);
  const [filterType, setFilterType] = useState('all'); // 'all', 'purchases', 'transfers'

  useEffect(() => {
    loadAllData();
  }, []);

  useEffect(() => {
    // Merge and sort transactions when data changes
    const merged = [];
    
    // Add purchase transactions with type marker
    if (filterType === 'all' || filterType === 'purchases') {
      const purchaseTransactions = transactions.map(t => ({
        ...t,
        transactionType: 'purchase',
        sortDate: new Date(t.created_at)
      }));
      merged.push(...purchaseTransactions);
    }
    
    // Add balance transactions with type marker
    if (filterType === 'all' || filterType === 'transfers') {
      const transferTransactions = balanceTransactions.map(t => ({
        ...t,
        transactionType: 'transfer',
        sortDate: new Date(t.created_at)
      }));
      merged.push(...transferTransactions);
    }
    
    // Sort by date (newest first)
    merged.sort((a, b) => b.sortDate - a.sortDate);
    
    setAllTransactions(merged);
  }, [transactions, balanceTransactions, filterType]);

  const loadAllData = async () => {
    setLoading(true);
    await Promise.all([
      loadTransactions(1, false),
      loadBalanceTransactions(1, false)
    ]);
    setLoading(false);
  };

  const loadTransactions = async (pageNum = 1, append = false) => {
    try {
      const response = await accountService.getTransactionHistory(pageNum, 10);
      if (response.success) {
        if (append) {
          setTransactions([...transactions, ...response.transactions]);
        } else {
          setTransactions(response.transactions);
        }
        setPagination(response.pagination);
        setHasMore(response.pagination.has_next);
      }
    } catch (error) {
      console.error('Error loading transactions:', error);
      if (!append) {
        Alert.alert('Error', error.toString());
      }
    } finally {
      if (!append) {
        setRefreshing(false);
      }
    }
  };

  const loadBalanceTransactions = async (pageNum = 1, append = false) => {
    try {
      const response = await accountService.getBalanceTransactions(pageNum, 10);
      if (response.success) {
        if (append) {
          setBalanceTransactions([...balanceTransactions, ...response.balance_transactions]);
        } else {
          setBalanceTransactions(response.balance_transactions);
        }
        setBalancePagination(response.pagination);
        setHasMoreBalance(response.pagination.has_next);
      }
    } catch (error) {
      console.error('Error loading balance transactions:', error);
      if (!append) {
        Alert.alert('Error', error.toString());
      }
    }
  };


  const handleRefresh = () => {
    setRefreshing(true);
    setPage(1);
    setBalancePage(1);
    setShowAll(false);
    setDropdownVisible(false);
    loadAllData();
  };

  const loadMore = () => {
    if (!loading && showAll) {
      if (filterType === 'all') {
        // Load more from both if needed
        if (hasMore) {
          const nextPage = page + 1;
          setPage(nextPage);
          loadTransactions(nextPage, true);
        }
        if (hasMoreBalance) {
          const nextBalancePage = balancePage + 1;
          setBalancePage(nextBalancePage);
          loadBalanceTransactions(nextBalancePage, true);
        }
      } else if (filterType === 'purchases' && hasMore) {
        const nextPage = page + 1;
        setPage(nextPage);
        loadTransactions(nextPage, true);
      } else if (filterType === 'transfers' && hasMoreBalance) {
        const nextBalancePage = balancePage + 1;
        setBalancePage(nextBalancePage);
        loadBalanceTransactions(nextBalancePage, true);
      }
    }
  };

  const handleViewAll = async () => {
    setShowAll(true);
    setLoading(true);
    setPage(1);
    setBalancePage(1);
    await Promise.all([
      loadTransactions(1, false),
      loadBalanceTransactions(1, false)
    ]);
    setDropdownVisible(false);
  };

  const handleViewRecent = async () => {
    setShowAll(false);
    setLoading(true);
    setPage(1);
    setBalancePage(1);
    await Promise.all([
      loadTransactions(1, false),
      loadBalanceTransactions(1, false)
    ]);
    setDropdownVisible(false);
  };

  const handleDropdownSelect = (option) => {
    if (option === 'all') {
      handleViewAll();
    } else {
      handleViewRecent();
    }
  };

  const formatCurrency = (amount) => {
    const num = parseFloat(amount || 0);
    return `₱${num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  const formatDateTime = (dateString) => {
    const date = new Date(dateString);
    return date.toLocaleString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const getPaymentMethodStyle = (paymentMethod) => {
    switch (paymentMethod) {
      case 'debit':
        return { backgroundColor: colors.debit, label: 'DEBIT' };
      case 'cash':
        return { backgroundColor: colors.cash, label: 'CASH' };
      default:
        return { backgroundColor: colors.muted, label: 'OTHER' };
    }
  };

  const getStatusStyle = (status) => {
    switch (status) {
      case 'completed':
        return { backgroundColor: colors.success, label: 'COMPLETED' };
      case 'pending':
        return { backgroundColor: colors.warning, label: 'PENDING' };
      case 'cancelled':
        return { backgroundColor: colors.error, label: 'CANCELLED' };
      default:
        return { backgroundColor: colors.muted, label: status?.toUpperCase() || 'UNKNOWN' };
    }
  };

  const renderTransaction = ({ item }) => {
    // Render balance transaction (fund transfer)
    if (item.transactionType === 'transfer') {
      const isDeposit = item.transaction_type === 'deposit';
      const isDeduction = item.transaction_type === 'deduction';
      
      return (
        <View style={styles.transactionCard}>
          <View style={styles.transactionHeader}>
            <View style={styles.transactionInfo}>
              <View style={styles.transactionNumberRow}>
                <View style={[
                  styles.transactionTypeBadge,
                  { backgroundColor: isDeposit ? colors.success : colors.error }
                ]}>
                  <Text style={styles.transactionTypeBadgeText}>
                    {isDeposit ? 'DEPOSIT' : 'DEDUCTION'}
                  </Text>
                </View>
                <View style={[styles.statusBadge, { backgroundColor: colors.success }]}>
                  <Text style={styles.statusBadgeText}>COMPLETED</Text>
                </View>
              </View>
              <Text style={styles.transactionDate}>{formatDateTime(item.created_at)}</Text>
            </View>
            <Text style={[
              styles.transactionAmount,
              isDeposit ? styles.depositAmount : styles.deductionAmount
            ]}>
              {isDeposit ? '+' : '-'}{formatCurrency(item.amount)}
            </Text>
          </View>
          <View style={styles.transactionDetails}>
            <View style={styles.balanceInfoRow}>
              <Text style={styles.balanceLabel}>Balance Before:</Text>
              <Text style={styles.balanceValue}>{formatCurrency(item.balance_before)}</Text>
            </View>
            <View style={styles.balanceInfoRow}>
              <Text style={styles.balanceLabel}>Balance After:</Text>
              <Text style={[styles.balanceValue, styles.balanceAfter]}>
                {formatCurrency(item.balance_after)}
              </Text>
            </View>
            {item.notes && (
              <View style={styles.notesContainer}>
                <Text style={styles.notesText}>{item.notes}</Text>
              </View>
            )}
          </View>
        </View>
      );
    }

    // Render purchase transaction
    const paymentStyle = getPaymentMethodStyle(item.payment_method);
    const statusStyle = getStatusStyle(item.status);

    return (
      <View style={styles.transactionCard}>
        <View style={styles.transactionHeader}>
          <View style={styles.transactionInfo}>
            <View style={styles.transactionNumberRow}>
              <Text style={styles.transactionNumber}>{item.transaction_number}</Text>
              <View style={[styles.statusBadge, { backgroundColor: statusStyle.backgroundColor }]}>
                <Text style={styles.statusBadgeText}>{statusStyle.label}</Text>
              </View>
            </View>
            <Text style={styles.transactionDate}>{formatDateTime(item.created_at)}</Text>
          </View>
          <Text style={styles.transactionAmount}>{formatCurrency(item.total_amount)}</Text>
        </View>
        <View style={styles.transactionDetails}>
          <View style={styles.paymentMethodRow}>
            <View style={[styles.paymentBadge, { backgroundColor: paymentStyle.backgroundColor }]}>
              <Text style={styles.paymentBadgeText}>{paymentStyle.label}</Text>
            </View>
            <Text style={styles.detailText}>
              {item.payment_method_display}
            </Text>
          </View>
          {item.items && item.items.length > 0 && (
            <Text style={styles.itemsText}>
              {item.items.length} item{item.items.length > 1 ? 's' : ''}
            </Text>
          )}
        </View>
      </View>
    );
  };

  const renderFilterTabs = () => {
    return (
      <View style={styles.filterTabsContainer}>
        <TouchableOpacity
          style={[styles.filterTab, filterType === 'all' && styles.filterTabActive]}
          onPress={() => setFilterType('all')}
        >
          <Text style={[styles.filterTabText, filterType === 'all' && styles.filterTabTextActive]}>
            All
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.filterTab, filterType === 'purchases' && styles.filterTabActive]}
          onPress={() => setFilterType('purchases')}
        >
          <Text style={[styles.filterTabText, filterType === 'purchases' && styles.filterTabTextActive]}>
            Purchases
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.filterTab, filterType === 'transfers' && styles.filterTabActive]}
          onPress={() => setFilterType('transfers')}
        >
          <Text style={[styles.filterTabText, filterType === 'transfers' && styles.filterTabTextActive]}>
            Transfers
          </Text>
        </TouchableOpacity>
      </View>
    );
  };

  const renderDropdown = () => {
    return (
      <View style={styles.dropdownContainer}>
        <TouchableOpacity
          style={styles.dropdownButton}
          onPress={() => setDropdownVisible(!dropdownVisible)}
        >
          <Text style={styles.dropdownButtonText}>
            {showAll ? 'View All Transactions' : 'Recent 10 Transactions'}
          </Text>
          <Text style={styles.dropdownArrow}>{dropdownVisible ? '▲' : '▼'}</Text>
        </TouchableOpacity>
        
        <Modal
          transparent={true}
          visible={dropdownVisible}
          animationType="fade"
          onRequestClose={() => setDropdownVisible(false)}
        >
          <TouchableWithoutFeedback onPress={() => setDropdownVisible(false)}>
            <View style={styles.modalOverlay}>
              <TouchableWithoutFeedback>
                <View style={styles.dropdownMenu}>
                  <TouchableOpacity
                    style={[
                      styles.dropdownOption,
                      !showAll && styles.dropdownOptionActive
                    ]}
                    onPress={() => handleDropdownSelect('recent')}
                  >
                    <Text style={[
                      styles.dropdownOptionText,
                      !showAll && styles.dropdownOptionTextActive
                    ]}>
                      Recent 10 Transactions
                    </Text>
                    {!showAll && <Text style={styles.checkmark}>✓</Text>}
                  </TouchableOpacity>
                  <View style={styles.dropdownDivider} />
                  <TouchableOpacity
                    style={[
                      styles.dropdownOption,
                      showAll && styles.dropdownOptionActive
                    ]}
                    onPress={() => handleDropdownSelect('all')}
                  >
                    <Text style={[
                      styles.dropdownOptionText,
                      showAll && styles.dropdownOptionTextActive
                    ]}>
                      View All Transactions
                    </Text>
                    {showAll && <Text style={styles.checkmark}>✓</Text>}
                  </TouchableOpacity>
                </View>
              </TouchableWithoutFeedback>
            </View>
          </TouchableWithoutFeedback>
        </Modal>
      </View>
    );
  };

  const hasMoreToLoad = () => {
    if (filterType === 'all') {
      return (hasMore || hasMoreBalance) && showAll;
    } else if (filterType === 'purchases') {
      return hasMore && showAll;
    } else if (filterType === 'transfers') {
      return hasMoreBalance && showAll;
    }
    return false;
  };

  if (loading && allTransactions.length === 0) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.brand} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {renderFilterTabs()}
      {renderDropdown()}
      <FlatList
        data={allTransactions}
        renderItem={renderTransaction}
        keyExtractor={(item) => `${item.transactionType}-${item.id}`}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={handleRefresh} />
        }
        onEndReached={showAll ? loadMore : null}
        onEndReachedThreshold={0.5}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyText}>No transactions found</Text>
          </View>
        }
        ListFooterComponent={
          hasMoreToLoad() ? (
            <View style={styles.footer}>
              <ActivityIndicator size="small" color={colors.brand} />
            </View>
          ) : null
        }
      />
    </View>
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
  dropdownContainer: {
    paddingHorizontal: 15,
    paddingTop: 15,
    paddingBottom: 10,
    backgroundColor: colors.background,
    zIndex: 1000,
  },
  dropdownButton: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: colors.panel,
    borderRadius: 8,
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderWidth: 1,
    borderColor: colors.border,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  dropdownButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: colors.textPrimary,
  },
  dropdownArrow: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'flex-start',
    paddingTop: 70,
    paddingHorizontal: 15,
  },
  dropdownMenu: {
    backgroundColor: colors.panel,
    borderRadius: 8,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 8,
    elevation: 5,
    overflow: 'hidden',
  },
  dropdownOption: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 14,
    paddingHorizontal: 16,
  },
  dropdownOptionActive: {
    backgroundColor: colors.background,
  },
  dropdownOptionText: {
    fontSize: 16,
    color: colors.textPrimary,
  },
  dropdownOptionTextActive: {
    fontWeight: '600',
    color: colors.brand,
  },
  dropdownDivider: {
    height: 1,
    backgroundColor: colors.borderLight,
  },
  checkmark: {
    fontSize: 16,
    color: colors.brand,
    fontWeight: 'bold',
  },
  transactionCard: {
    backgroundColor: colors.panel,
    margin: 15,
    marginBottom: 0,
    borderRadius: 12,
    padding: 15,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  transactionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 10,
  },
  transactionInfo: {
    flex: 1,
  },
  transactionNumberRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 4,
    flexWrap: 'wrap',
  },
  transactionNumber: {
    fontSize: 16,
    fontWeight: 'bold',
    color: colors.textPrimary,
    marginRight: 8,
  },
  statusBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 4,
    marginLeft: 4,
  },
  statusBadgeText: {
    color: colors.textWhite,
    fontSize: 10,
    fontWeight: '600',
    letterSpacing: 0.5,
  },
  transactionDate: {
    fontSize: 14,
    color: colors.textSecondary,
  },
  transactionAmount: {
    fontSize: 20,
    fontWeight: 'bold',
    color: colors.brand,
  },
  transactionDetails: {
    borderTopWidth: 1,
    borderTopColor: colors.borderLight,
    paddingTop: 10,
    marginTop: 10,
  },
  paymentMethodRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
  },
  paymentBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 4,
    marginRight: 8,
  },
  paymentBadgeText: {
    color: colors.textWhite,
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
  detailText: {
    fontSize: 14,
    color: colors.textSecondary,
    flex: 1,
  },
  itemsText: {
    fontSize: 14,
    color: colors.accent,
    marginTop: 4,
  },
  emptyContainer: {
    padding: 40,
    alignItems: 'center',
  },
  emptyText: {
    fontSize: 16,
    color: colors.textSecondary,
  },
  footer: {
    padding: 20,
    alignItems: 'center',
  },
  viewAllButton: {
    backgroundColor: colors.brand,
    borderRadius: 8,
    paddingVertical: 12,
    paddingHorizontal: 24,
    alignItems: 'center',
  },
  viewAllButtonText: {
    color: colors.textWhite,
    fontSize: 16,
    fontWeight: '600',
  },
  filterTabsContainer: {
    flexDirection: 'row',
    paddingHorizontal: 15,
    paddingTop: 15,
    paddingBottom: 10,
    backgroundColor: colors.background,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  filterTab: {
    flex: 1,
    paddingVertical: 10,
    paddingHorizontal: 15,
    marginHorizontal: 4,
    borderRadius: 8,
    backgroundColor: colors.panel,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: 'center',
  },
  filterTabActive: {
    backgroundColor: colors.brand,
    borderColor: colors.brand,
  },
  filterTabText: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  filterTabTextActive: {
    color: colors.textWhite,
  },
  transactionTypeBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 4,
    marginRight: 8,
  },
  transactionTypeBadgeText: {
    color: colors.textWhite,
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
  depositAmount: {
    color: colors.success,
  },
  deductionAmount: {
    color: colors.error,
  },
  balanceInfoRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 6,
  },
  balanceLabel: {
    fontSize: 14,
    color: colors.textSecondary,
  },
  balanceValue: {
    fontSize: 16,
    fontWeight: '600',
    color: colors.textPrimary,
  },
  balanceAfter: {
    fontSize: 18,
    fontWeight: 'bold',
    color: colors.brand,
  },
  notesContainer: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: colors.borderLight,
  },
  notesText: {
    fontSize: 14,
    color: colors.textSecondary,
    fontStyle: 'italic',
  },
});

