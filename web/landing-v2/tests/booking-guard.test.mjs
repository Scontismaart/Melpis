import assert from 'node:assert/strict';
import test from 'node:test';
import { bookingAllowed } from '../src/components/booking-guard.mjs';

test('illustrative booking requires enough verified capacity and explicit customer confirmation', () => {
  const valid = { capacity: 2, partySize: 2, availabilityVerified: true, customerConfirmed: true };
  assert.equal(bookingAllowed(valid), true);
  assert.equal(bookingAllowed({ ...valid, capacity: 1 }), false, 'insufficient capacity fails closed');
  assert.equal(bookingAllowed({ ...valid, capacity: 0 }), false, 'zero capacity fails closed');
  assert.equal(bookingAllowed({ ...valid, capacity: null, availabilityVerified: false }), false, 'unknown availability fails closed');
  assert.equal(bookingAllowed({ ...valid, customerConfirmed: false }), false, 'no explicit confirmation fails closed');
  assert.equal(bookingAllowed({ ...valid, capacity: 2.5 }), false, 'non-integer fixture capacity fails closed');
});
