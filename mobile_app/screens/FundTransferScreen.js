import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TextInput,
  TouchableOpacity,
  Alert,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Modal,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { fundTransferService, accountService } from '../services/api';
import { colors } from '../constants/colors';

export default function FundTransferScreen({ navigation }) {
  const [recipientRfid, setRecipientRfid] = useState('');
  const [amount, setAmount] = useState('');
  const [notes, setNotes] = useState('');
  const [recipient, setRecipient] = useState(null);
  const [searching, setSearching] = useState(false);
  const [currentBalance, setCurrentBalance] = useState(null);
  const [searchTimeout, setSearchTimeout] = useState(null);
  const [showTransactionDetails, setShowTransactionDetails] = useState(false);
  const [transactionData, setTransactionData] = useState(null);
  const [showOTPModal, setShowOTPModal] = useState(false);
  const [otpCode, setOtpCode] = useState('');
  const [requestingOTP, setRequestingOTP] = useState(false);
  const [verifyingOTP, setVerifyingOTP] = useState(false);
  const [otpExpiresIn, setOtpExpiresIn] = useState(null);
  const [otpTimer, setOtpTimer] = useState(null);

  useEffect(() => {
    loadCurrentBalance();
    
    // Cleanup timer on unmount
    return () => {
      if (otpTimer) {
        clearInterval(otpTimer);
      }
    };
  }, []);

  useEffect(() => {
    // Auto-search when RFID is entered (with debounce)
    if (recipientRfid.trim().length >= 3) {
      if (searchTimeout) {
        clearTimeout(searchTimeout);
      }
      const timeout = setTimeout(() => {
        handleSearchMember();
      }, 500);
      setSearchTimeout(timeout);
    } else {
      setRecipient(null);
    }

    return () => {
      if (searchTimeout) {
        clearTimeout(searchTimeout);
      }
    };
  }, [recipientRfid]);

  const loadCurrentBalance = async () => {
    try {
      const response = await accountService.getAccountInfo();
      if (response.success && response.member) {
        setCurrentBalance(parseFloat(response.member.balance));
      }
    } catch (error) {
      console.error('Failed to load balance:', error);
    }
  };

  const handleSearchMember = async () => {
    const rfid = recipientRfid.trim();
    if (!rfid || rfid.length < 3) {
      setRecipient(null);
      return;
    }

    setSearching(true);
    try {
      const response = await fundTransferService.searchMember(rfid);
      if (response.success && response.member) {
        setRecipient(response.member);
      } else {
        setRecipient(null);
      }
    } catch (error) {
      setRecipient(null);
      const errorMessage = typeof error === 'string' ? error : error.message || 'Member not found';
      // Don't show alert for "not found" - just clear recipient
      if (!errorMessage.includes('not found')) {
        Alert.alert('Error', errorMessage);
      }
    } finally {
      setSearching(false);
    }
  };

  const handleTransfer = async () => {
    // Validation
    if (!recipient) {
      Alert.alert('Error', 'Please search and select a recipient first');
      return;
    }

    const transferAmount = parseFloat(amount);
    if (!amount || isNaN(transferAmount) || transferAmount <= 0) {
      Alert.alert('Error', 'Please enter a valid amount greater than zero');
      return;
    }

    if (transferAmount > currentBalance) {
      Alert.alert('Error', 'Insufficient balance');
      return;
    }

    // Confirm transfer
    Alert.alert(
      'Confirm Transfer',
      `Transfer ₱${transferAmount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} to ${recipient.full_name}?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Continue',
          style: 'default',
          onPress: async () => {
            // Request OTP
            await requestOTP();
          },
        },
      ]
    );
  };

  const requestOTP = async () => {
    const transferAmount = parseFloat(amount);
    setRequestingOTP(true);
    try {
      const response = await fundTransferService.requestTransferOTP(
        recipientRfid,
        transferAmount,
        notes
      );
      
      if (response.success) {
        // Show OTP modal
        setShowOTPModal(true);
        setOtpCode('');
        
        // Set expiration timer
        const expiresIn = response.expires_in || 600; // 10 minutes default
        setOtpExpiresIn(expiresIn);
        
        // Start countdown timer
        startOTPTimer(expiresIn);
      }
    } catch (error) {
      const errorMessage = typeof error === 'string' ? error : error.message || 'Failed to send OTP';
      Alert.alert('Error', errorMessage);
    } finally {
      setRequestingOTP(false);
    }
  };

  const startOTPTimer = (seconds) => {
    // Clear existing timer
    if (otpTimer) {
      clearInterval(otpTimer);
    }
    
    let remaining = seconds;
    setOtpExpiresIn(remaining);
    
    const timer = setInterval(() => {
      remaining -= 1;
      setOtpExpiresIn(remaining);
      
      if (remaining <= 0) {
        clearInterval(timer);
        setOtpTimer(null);
        Alert.alert('OTP Expired', 'The OTP code has expired. Please request a new one.');
        setShowOTPModal(false);
        setOtpCode('');
      }
    }, 1000);
    
    setOtpTimer(timer);
  };

  const handleVerifyOTP = async () => {
    if (!otpCode || otpCode.length !== 6) {
      Alert.alert('Error', 'Please enter a valid 6-digit OTP code');
      return;
    }

    setVerifyingOTP(true);
    try {
      const response = await fundTransferService.verifyTransferOTP(otpCode);
      
      if (response.success) {
        // Clear timer
        if (otpTimer) {
          clearInterval(otpTimer);
          setOtpTimer(null);
        }
        
        // Store transaction data for display
        setTransactionData(response);
        setShowTransactionDetails(true);
        setShowOTPModal(false);
        setOtpCode('');
        setOtpExpiresIn(null);
        
        // Reset form
        setRecipientRfid('');
        setAmount('');
        setNotes('');
        setRecipient(null);
        
        // Reload balance
        loadCurrentBalance();
      }
    } catch (error) {
      const errorMessage = typeof error === 'string' ? error : error.message || 'OTP verification failed';
      Alert.alert('Error', errorMessage);
    } finally {
      setVerifyingOTP(false);
    }
  };

  const formatTime = (seconds) => {
    if (!seconds || seconds < 0) return '00:00';
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
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
      second: '2-digit',
    });
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <ScrollView style={styles.scrollView} contentContainerStyle={styles.scrollContent}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.headerTitle}>Fund Transfer</Text>
        </View>

        {/* Current Balance Card */}
        <View style={styles.balanceCard}>
          <Text style={styles.balanceLabel}>Available Balance</Text>
          <Text style={styles.balanceAmount}>
            {currentBalance !== null ? formatCurrency(currentBalance) : 'Loading...'}
          </Text>
        </View>

        {/* Recipient Search Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Recipient</Text>
          <View style={styles.searchContainer}>
            <Ionicons name="search-outline" size={20} color={colors.textSecondary} style={styles.searchIcon} />
            <TextInput
              style={styles.searchInput}
              placeholder="Enter recipient RFID card number"
              value={recipientRfid}
              onChangeText={setRecipientRfid}
              autoCapitalize="none"
              autoCorrect={false}
            />
            {searching && (
              <ActivityIndicator size="small" color={colors.brand} style={styles.searchLoader} />
            )}
          </View>

          {/* Recipient Info */}
          {recipient && (
            <View style={styles.recipientCard}>
              <View style={styles.recipientHeader}>
                <Ionicons name="person-circle" size={40} color={colors.brand} />
                <View style={styles.recipientInfo}>
                  <Text style={styles.recipientName}>{recipient.full_name}</Text>
                  <Text style={styles.recipientRfid}>RFID: {recipient.rfid_card_number}</Text>
                  {recipient.member_type_name && (
                    <Text style={styles.recipientType}>{recipient.member_type_name}</Text>
                  )}
                </View>
              </View>
            </View>
          )}

          {recipientRfid.trim().length >= 3 && !recipient && !searching && (
            <View style={styles.noRecipient}>
              <Text style={styles.noRecipientText}>No member found with this RFID</Text>
            </View>
          )}
        </View>

        {/* Amount Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Amount</Text>
          <View style={styles.amountContainer}>
            <Text style={styles.currencySymbol}>₱</Text>
            <TextInput
              style={styles.amountInput}
              placeholder="0.00"
              value={amount}
              onChangeText={(text) => {
                // Allow only numbers and one decimal point
                const cleaned = text.replace(/[^0-9.]/g, '');
                // Ensure only one decimal point
                const parts = cleaned.split('.');
                if (parts.length > 2) {
                  return;
                }
                // Limit decimal places to 2
                if (parts[1] && parts[1].length > 2) {
                  return;
                }
                setAmount(cleaned);
              }}
              keyboardType="decimal-pad"
            />
          </View>
          {amount && !isNaN(parseFloat(amount)) && parseFloat(amount) > 0 && (
            <View style={styles.amountPreview}>
              <Text style={styles.amountPreviewText}>
                You will transfer: {formatCurrency(amount)}
              </Text>
              {currentBalance !== null && parseFloat(amount) > currentBalance && (
                <Text style={styles.insufficientText}>Insufficient balance</Text>
              )}
            </View>
          )}
        </View>

        {/* Notes Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Notes (Optional)</Text>
          <TextInput
            style={styles.notesInput}
            placeholder="Add a note for this transfer"
            value={notes}
            onChangeText={setNotes}
            multiline
            numberOfLines={3}
            maxLength={500}
          />
        </View>

        {/* Transfer Button */}
        <TouchableOpacity
          style={[
            styles.transferButton,
            (!recipient || !amount || requestingOTP || (currentBalance !== null && parseFloat(amount) > currentBalance)) &&
              styles.transferButtonDisabled,
          ]}
          onPress={handleTransfer}
          disabled={!recipient || !amount || requestingOTP || (currentBalance !== null && parseFloat(amount) > currentBalance)}
        >
          {requestingOTP ? (
            <ActivityIndicator size="small" color={colors.textWhite} />
          ) : (
            <>
              <Ionicons name="send-outline" size={20} color={colors.textWhite} />
              <Text style={styles.transferButtonText}>Transfer Funds</Text>
            </>
          )}
        </TouchableOpacity>
      </ScrollView>

      {/* OTP Verification Modal */}
      <Modal
        visible={showOTPModal}
        transparent={true}
        animationType="slide"
        onRequestClose={() => {
          if (otpTimer) {
            clearInterval(otpTimer);
            setOtpTimer(null);
          }
          setShowOTPModal(false);
          setOtpCode('');
          setOtpExpiresIn(null);
        }}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.otpModalContent}>
            <View style={styles.otpModalHeader}>
              <Text style={styles.otpModalTitle}>Enter Verification Code</Text>
              <TouchableOpacity
                onPress={() => {
                  if (otpTimer) {
                    clearInterval(otpTimer);
                    setOtpTimer(null);
                  }
                  setShowOTPModal(false);
                  setOtpCode('');
                  setOtpExpiresIn(null);
                }}
                style={styles.closeButton}
              >
                <Ionicons name="close" size={24} color={colors.textPrimary} />
              </TouchableOpacity>
            </View>

            <View style={styles.otpModalBody}>
              <Ionicons name="mail-outline" size={60} color={colors.brand} style={styles.otpIcon} />
              
              <Text style={styles.otpInstructionText}>
                We've sent a 6-digit verification code to your email address.
              </Text>

              {otpExpiresIn !== null && (
                <View style={styles.otpTimerContainer}>
                  <Ionicons name="time-outline" size={16} color={colors.textSecondary} />
                  <Text style={styles.otpTimerText}>
                    Code expires in: {formatTime(otpExpiresIn)}
                  </Text>
                </View>
              )}

              <View style={styles.otpInputContainer}>
                <TextInput
                  style={styles.otpInput}
                  placeholder="000000"
                  value={otpCode}
                  onChangeText={(text) => {
                    // Only allow digits and limit to 6 characters
                    const cleaned = text.replace(/[^0-9]/g, '').slice(0, 6);
                    setOtpCode(cleaned);
                  }}
                  keyboardType="number-pad"
                  maxLength={6}
                  autoFocus={true}
                  textAlign="center"
                />
              </View>

              <TouchableOpacity
                style={[
                  styles.verifyButton,
                  (!otpCode || otpCode.length !== 6 || verifyingOTP) && styles.verifyButtonDisabled,
                ]}
                onPress={handleVerifyOTP}
                disabled={!otpCode || otpCode.length !== 6 || verifyingOTP}
              >
                {verifyingOTP ? (
                  <ActivityIndicator size="small" color={colors.textWhite} />
                ) : (
                  <Text style={styles.verifyButtonText}>Verify & Transfer</Text>
                )}
              </TouchableOpacity>

              <TouchableOpacity
                style={styles.resendButton}
                onPress={requestOTP}
                disabled={requestingOTP}
              >
                <Text style={styles.resendButtonText}>
                  {requestingOTP ? 'Sending...' : 'Resend Code'}
                </Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* Transaction Details Modal */}
      <Modal
        visible={showTransactionDetails}
        transparent={true}
        animationType="slide"
        onRequestClose={() => setShowTransactionDetails(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Transfer Complete</Text>
              <TouchableOpacity
                onPress={() => setShowTransactionDetails(false)}
                style={styles.closeButton}
              >
                <Ionicons name="close" size={24} color={colors.textPrimary} />
              </TouchableOpacity>
            </View>

            {transactionData && (
              <ScrollView style={styles.modalScrollView} showsVerticalScrollIndicator={false}>
                {/* Success Message */}
                <View style={styles.successMessage}>
                  <Ionicons name="checkmark-circle" size={60} color={colors.success} />
                  <Text style={styles.successText}>
                    {transactionData.message || 'Fund transfer completed successfully'}
                  </Text>
                </View>

                {/* Transaction Summary */}
                <View style={styles.transactionSection}>
                  <Text style={styles.sectionHeader}>Transaction Summary</Text>
                  
                  <View style={styles.summaryRow}>
                    <Text style={styles.summaryLabel}>Amount Transferred</Text>
                    <Text style={styles.summaryValue}>
                      {formatCurrency(transactionData.transfer?.amount || 0)}
                    </Text>
                  </View>

                  <View style={styles.summaryRow}>
                    <Text style={styles.summaryLabel}>Recipient</Text>
                    <Text style={styles.summaryValue}>
                      {transactionData.transfer?.recipient?.full_name || 'N/A'}
                    </Text>
                  </View>

                  <View style={styles.summaryRow}>
                    <Text style={styles.summaryLabel}>RFID</Text>
                    <Text style={styles.summaryValue}>
                      {transactionData.transfer?.recipient?.rfid_card_number || 'N/A'}
                    </Text>
                  </View>

                  {transactionData.transfer?.notes && (
                    <View style={styles.summaryRow}>
                      <Text style={styles.summaryLabel}>Notes</Text>
                      <Text style={styles.summaryValue}>
                        {transactionData.transfer.notes}
                      </Text>
                    </View>
                  )}
                </View>

                {/* Sender Transaction Details */}
                {transactionData.sender_transaction && (
                  <View style={styles.transactionSection}>
                    <Text style={styles.sectionHeader}>Your Account</Text>
                    
                    <View style={styles.transactionCard}>
                      <View style={styles.transactionTypeRow}>
                        <View style={[styles.transactionTypeBadge, { backgroundColor: colors.error }]}>
                          <Text style={styles.transactionTypeText}>DEDUCTION</Text>
                        </View>
                        <Text style={styles.transactionAmount}>
                          -{formatCurrency(transactionData.sender_transaction.amount)}
                        </Text>
                      </View>

                      <View style={styles.balanceRow}>
                        <Text style={styles.balanceLabel}>Balance Before:</Text>
                        <Text style={styles.balanceValue}>
                          {formatCurrency(transactionData.sender_transaction.balance_before)}
                        </Text>
                      </View>

                      <View style={styles.balanceRow}>
                        <Text style={styles.balanceLabel}>Balance After:</Text>
                        <Text style={[styles.balanceValue, styles.balanceAfter]}>
                          {formatCurrency(transactionData.sender_transaction.balance_after)}
                        </Text>
                      </View>

                      <View style={styles.transactionNotes}>
                        <Text style={styles.notesLabel}>Transaction Notes:</Text>
                        <Text style={styles.notesText}>
                          {transactionData.sender_transaction.notes}
                        </Text>
                      </View>

                      <View style={styles.transactionDate}>
                        <Text style={styles.dateText}>
                          {formatDateTime(transactionData.sender_transaction.created_at)}
                        </Text>
                      </View>
                    </View>
                  </View>
                )}

                {/* Recipient Transaction Details */}
                {transactionData.recipient_transaction && (
                  <View style={styles.transactionSection}>
                    <Text style={styles.sectionHeader}>Recipient Account</Text>
                    
                    <View style={styles.transactionCard}>
                      <View style={styles.transactionTypeRow}>
                        <View style={[styles.transactionTypeBadge, { backgroundColor: colors.success }]}>
                          <Text style={styles.transactionTypeText}>DEPOSIT</Text>
                        </View>
                        <Text style={[styles.transactionAmount, styles.depositAmount]}>
                          +{formatCurrency(transactionData.recipient_transaction.amount)}
                        </Text>
                      </View>

                      <View style={styles.balanceRow}>
                        <Text style={styles.balanceLabel}>Balance Before:</Text>
                        <Text style={styles.balanceValue}>
                          {formatCurrency(transactionData.recipient_transaction.balance_before)}
                        </Text>
                      </View>

                      <View style={styles.balanceRow}>
                        <Text style={styles.balanceLabel}>Balance After:</Text>
                        <Text style={[styles.balanceValue, styles.balanceAfter]}>
                          {formatCurrency(transactionData.recipient_transaction.balance_after)}
                        </Text>
                      </View>

                      <View style={styles.transactionNotes}>
                        <Text style={styles.notesLabel}>Transaction Notes:</Text>
                        <Text style={styles.notesText}>
                          {transactionData.recipient_transaction.notes}
                        </Text>
                      </View>

                      <View style={styles.transactionDate}>
                        <Text style={styles.dateText}>
                          {formatDateTime(transactionData.recipient_transaction.created_at)}
                        </Text>
                      </View>
                    </View>
                  </View>
                )}

                {/* Close Button */}
                <TouchableOpacity
                  style={styles.doneButton}
                  onPress={() => setShowTransactionDetails(false)}
                >
                  <Text style={styles.doneButtonText}>Done</Text>
                </TouchableOpacity>
              </ScrollView>
            )}
          </View>
        </View>
      </Modal>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  scrollView: {
    flex: 1,
  },
  scrollContent: {
    paddingBottom: 20,
  },
  header: {
    backgroundColor: colors.brand,
    padding: 20,
    paddingTop: 60,
  },
  headerTitle: {
    fontSize: 24,
    fontWeight: 'bold',
    color: colors.textWhite,
  },
  balanceCard: {
    backgroundColor: colors.panel,
    margin: 15,
    marginTop: 15,
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
    fontSize: 32,
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
  sectionTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: colors.textPrimary,
    marginBottom: 15,
  },
  searchContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.background,
    borderRadius: 8,
    paddingHorizontal: 15,
    borderWidth: 1,
    borderColor: colors.border,
  },
  searchIcon: {
    marginRight: 10,
  },
  searchInput: {
    flex: 1,
    fontSize: 16,
    color: colors.textPrimary,
    paddingVertical: 12,
  },
  searchLoader: {
    marginLeft: 10,
  },
  recipientCard: {
    marginTop: 15,
    padding: 15,
    backgroundColor: colors.background,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.brand,
  },
  recipientHeader: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  recipientInfo: {
    marginLeft: 12,
    flex: 1,
  },
  recipientName: {
    fontSize: 18,
    fontWeight: '600',
    color: colors.textPrimary,
    marginBottom: 4,
  },
  recipientRfid: {
    fontSize: 14,
    color: colors.textSecondary,
    marginBottom: 2,
  },
  recipientType: {
    fontSize: 12,
    color: colors.textMuted,
    marginTop: 2,
  },
  noRecipient: {
    marginTop: 15,
    padding: 15,
    backgroundColor: '#fff3cd',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#ffc107',
  },
  noRecipientText: {
    fontSize: 14,
    color: '#856404',
    textAlign: 'center',
  },
  amountContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.background,
    borderRadius: 8,
    paddingHorizontal: 15,
    borderWidth: 1,
    borderColor: colors.border,
  },
  currencySymbol: {
    fontSize: 24,
    fontWeight: 'bold',
    color: colors.textPrimary,
    marginRight: 8,
  },
  amountInput: {
    flex: 1,
    fontSize: 24,
    fontWeight: '600',
    color: colors.textPrimary,
    paddingVertical: 12,
  },
  amountPreview: {
    marginTop: 12,
    padding: 12,
    backgroundColor: '#e8f5e9',
    borderRadius: 8,
  },
  amountPreviewText: {
    fontSize: 16,
    fontWeight: '600',
    color: colors.brand,
  },
  insufficientText: {
    fontSize: 14,
    color: colors.error,
    marginTop: 4,
  },
  notesInput: {
    backgroundColor: colors.background,
    borderRadius: 8,
    padding: 15,
    fontSize: 16,
    color: colors.textPrimary,
    borderWidth: 1,
    borderColor: colors.border,
    textAlignVertical: 'top',
    minHeight: 80,
  },
  transferButton: {
    backgroundColor: colors.brand,
    margin: 15,
    marginTop: 0,
    borderRadius: 12,
    padding: 18,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 4,
    elevation: 3,
  },
  transferButtonDisabled: {
    backgroundColor: colors.muted,
    opacity: 0.6,
  },
  transferButtonText: {
    color: colors.textWhite,
    fontSize: 18,
    fontWeight: 'bold',
    marginLeft: 8,
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
    maxHeight: '90%',
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
  modalScrollView: {
    flex: 1,
  },
  successMessage: {
    alignItems: 'center',
    padding: 30,
    paddingBottom: 20,
  },
  successText: {
    fontSize: 18,
    fontWeight: '600',
    color: colors.textPrimary,
    marginTop: 15,
    textAlign: 'center',
  },
  transactionSection: {
    paddingHorizontal: 20,
    marginBottom: 20,
  },
  sectionHeader: {
    fontSize: 18,
    fontWeight: 'bold',
    color: colors.textPrimary,
    marginBottom: 15,
  },
  summaryRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  summaryLabel: {
    fontSize: 16,
    color: colors.textSecondary,
    flex: 1,
  },
  summaryValue: {
    fontSize: 16,
    fontWeight: '600',
    color: colors.textPrimary,
    textAlign: 'right',
    flex: 1,
  },
  transactionCard: {
    backgroundColor: colors.background,
    borderRadius: 12,
    padding: 15,
    borderWidth: 1,
    borderColor: colors.border,
  },
  transactionTypeRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 15,
  },
  transactionTypeBadge: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 6,
  },
  transactionTypeText: {
    color: colors.textWhite,
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
  transactionAmount: {
    fontSize: 24,
    fontWeight: 'bold',
    color: colors.error,
  },
  depositAmount: {
    color: colors.success,
  },
  balanceRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
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
  transactionNotes: {
    marginTop: 15,
    paddingTop: 15,
    borderTopWidth: 1,
    borderTopColor: colors.borderLight,
  },
  notesLabel: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.textSecondary,
    marginBottom: 5,
  },
  notesText: {
    fontSize: 14,
    color: colors.textPrimary,
    lineHeight: 20,
  },
  transactionDate: {
    marginTop: 15,
    paddingTop: 15,
    borderTopWidth: 1,
    borderTopColor: colors.borderLight,
  },
  dateText: {
    fontSize: 12,
    color: colors.textMuted,
    textAlign: 'center',
  },
  doneButton: {
    backgroundColor: colors.brand,
    marginHorizontal: 20,
    marginTop: 10,
    marginBottom: 10,
    borderRadius: 12,
    padding: 18,
    alignItems: 'center',
  },
  doneButtonText: {
    color: colors.textWhite,
    fontSize: 18,
    fontWeight: 'bold',
  },
  otpModalContent: {
    backgroundColor: colors.panel,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    maxHeight: '80%',
    paddingBottom: 20,
  },
  otpModalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 20,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  otpModalTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    color: colors.textPrimary,
  },
  otpModalBody: {
    padding: 30,
    alignItems: 'center',
  },
  otpIcon: {
    marginBottom: 20,
  },
  otpInstructionText: {
    fontSize: 16,
    color: colors.textSecondary,
    textAlign: 'center',
    marginBottom: 20,
    lineHeight: 24,
  },
  otpTimerContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff3cd',
    paddingHorizontal: 15,
    paddingVertical: 8,
    borderRadius: 8,
    marginBottom: 25,
  },
  otpTimerText: {
    fontSize: 14,
    color: '#856404',
    marginLeft: 8,
    fontWeight: '600',
  },
  otpInputContainer: {
    width: '100%',
    marginBottom: 25,
  },
  otpInput: {
    backgroundColor: colors.background,
    borderRadius: 12,
    padding: 20,
    fontSize: 32,
    fontWeight: 'bold',
    color: colors.textPrimary,
    borderWidth: 2,
    borderColor: colors.brand,
    letterSpacing: 8,
  },
  verifyButton: {
    backgroundColor: colors.brand,
    borderRadius: 12,
    padding: 18,
    width: '100%',
    alignItems: 'center',
    marginBottom: 15,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 4,
    elevation: 3,
  },
  verifyButtonDisabled: {
    backgroundColor: colors.muted,
    opacity: 0.6,
  },
  verifyButtonText: {
    color: colors.textWhite,
    fontSize: 18,
    fontWeight: 'bold',
  },
  resendButton: {
    padding: 10,
  },
  resendButtonText: {
    color: colors.brand,
    fontSize: 16,
    fontWeight: '600',
  },
});

