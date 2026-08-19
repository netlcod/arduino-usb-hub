/*
   Copyright (c) 2015, Arduino LLC
   Original code (pre-library): Copyright (c) 2011, Peter Barrett

   Permission to use, copy, modify, and/or distribute this software for
   any purpose with or without fee is hereby granted, provided that the
   above copyright notice and this permission notice appear in all copies.

   THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL
   WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED
   WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR
   BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES
   OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS,
   WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION,
   ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS
   SOFTWARE.
 */

#include "HID.h"

#if defined(USBCON)

#if HID_COMMAND_ENABLED && HID_CAPABILITY_ENABLED
static const uint8_t _capabilityReport[] PROGMEM = {
	'H', 'C',
	HID_COMMAND_TRANSPORT,
	HID_COMMAND_REPORT_ID,
	HID_COMMAND_PAYLOAD_LEN,
	HID_COMMAND_QUEUE_SLOTS,
	HID_CAPABILITY_DEVICE_TYPE,
	HID_CAPABILITY_PROTOCOL_VERSION,
};
#endif

HID_& HID()
{
	static HID_ obj;
	return obj;
}

int HID_::getInterface(uint8_t* interfaceCount)
{
	*interfaceCount += 1; // uses 1
	HIDDescriptor hidInterface = {
		D_INTERFACE(pluggedInterface,
#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT
		            2,
#else
		            1,
#endif
		            USB_DEVICE_CLASS_HUMAN_INTERFACE, HID_SUBCLASS_BOOT_INTERFACE, HID_PROTOCOL_MOUSE),
		D_HIDREPORT(descriptorSize),
		D_ENDPOINT(USB_ENDPOINT_IN(pluggedEndpoint), USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, 0x01)
#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT
		,
		D_ENDPOINT(USB_ENDPOINT_OUT(pluggedEndpoint + 1), USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, 0x01)
#endif
	};
	return USB_SendControl(0, &hidInterface, sizeof(hidInterface));
}

int HID_::getDescriptor(USBSetup& setup)
{
	// Check if this is a HID Class Descriptor request
	if (setup.bmRequestType != REQUEST_DEVICETOHOST_STANDARD_INTERFACE) { return 0; }
	if (setup.wValueH != HID_REPORT_DESCRIPTOR_TYPE) { return 0; }

	// In a HID Class Descriptor wIndex contains the interface number
	if (setup.wIndex != pluggedInterface) { return 0; }

	int total = 0;
	HIDSubDescriptor* node;
	for (node = rootNode; node; node = node->next) {
		int res = USB_SendControl(TRANSFER_PGM, node->data, node->length);
		if (res == -1)
			return -1;
		total += res;
	}
	
	// Reset the protocol on reenumeration. Normally the host should not assume the state of the protocol
	// due to the USB specs, but Windows and Linux just assumes its in report mode.
	protocol = HID_REPORT_PROTOCOL;
	
	return total;
}

uint8_t HID_::getShortName(char *name)
{
	name[0] = 'H';
	name[1] = 'I';
	name[2] = 'D';
	name[3] = 'A' + (descriptorSize & 0x0F);
	name[4] = 'A' + ((descriptorSize >> 4) & 0x0F);
	return 5;
}

void HID_::AppendDescriptor(HIDSubDescriptor *node)
{
	if (!rootNode) {
		rootNode = node;
	} else {
		HIDSubDescriptor *current = rootNode;
		while (current->next) {
			current = current->next;
		}
		current->next = node;
	}
	descriptorSize += node->length;
}

int HID_::SendReport(uint8_t id, const void* data, int len)
{
	auto ret = USB_Send(pluggedEndpoint, &id, 1);
	if (ret < 0) return ret;
	auto ret2 = USB_Send(pluggedEndpoint | TRANSFER_RELEASE, data, len);
	if (ret2 < 0) return ret2;
	return ret + ret2;
}

bool HID_::setup(USBSetup& setup)
{
	if (pluggedInterface != setup.wIndex) {
		return false;
	}

	uint8_t request = setup.bRequest;
	uint8_t requestType = setup.bmRequestType;

	if (requestType == REQUEST_DEVICETOHOST_CLASS_INTERFACE)
	{
		if (request == HID_GET_REPORT) {
#if HID_COMMAND_ENABLED && HID_CAPABILITY_ENABLED
			if (setup.wValueH == HID_REPORT_TYPE_FEATURE &&
			    setup.wValueL == HID_CAPABILITY_REPORT_ID) {
				return USB_SendControl(TRANSFER_PGM, _capabilityReport,
				                        sizeof(_capabilityReport)) > 0;
			}
#endif
			// Unsupported report: stall instead of sending an
			// empty zero-length packet (previous behaviour).
			return false;
		}
		if (request == HID_GET_PROTOCOL) {
			// TODO: Send8(protocol);
			return true;
		}
		if (request == HID_GET_IDLE) {
			// TODO: Send8(idle);
		}
	}

	if (requestType == REQUEST_HOSTTODEVICE_CLASS_INTERFACE)
	{
		if (request == HID_SET_PROTOCOL) {
			// The USB Host tells us if we are in boot or report mode.
			// This only works with a real boot compatible device.
			protocol = setup.wValueL;
			return true;
		}
		if (request == HID_SET_IDLE) {
			idle = setup.wValueL;
			return true;
		}
		if (request == HID_SET_REPORT)
		{
#if HID_COMMAND_ENABLED
			// Command channel: accept Output and Feature reports carrying
			// the command report. Windows sends the full report buffer
			// (including the report id byte) as the data stage, so
			// wLength == TOTAL_LEN (16), not PAYLOAD_LEN (15). Only a
			// bounded copy into the ring happens here; commands are
			// consumed from loop() via readReportPacket().
			if (setup.wValueH == HID_REPORT_TYPE_OUTPUT ||
			    setup.wValueH == HID_REPORT_TYPE_FEATURE)
			{
				uint16_t length = setup.wLength;
				if (length > 0 && length <= HID_COMMAND_TOTAL_LEN) {
					uint8_t scratch[HID_COMMAND_TOTAL_LEN];
					USB_RecvControl(scratch, length);
					const uint8_t* payload = scratch;
					uint8_t payloadLen = length;
					uint8_t reportId = setup.wValueL;
					if (reportId == 0) {
						// Report id carried in the first data byte.
						if (scratch[0] != HID_COMMAND_REPORT_ID) {
							return false;
						}
						payload = scratch + 1;
						payloadLen = length - 1;
					} else if (reportId == HID_COMMAND_REPORT_ID &&
					           length == HID_COMMAND_TOTAL_LEN) {
						// Report id duplicated in the data stage.
						if (scratch[0] == HID_COMMAND_REPORT_ID) {
							payload = scratch + 1;
							payloadLen = length - 1;
						} else {
							payloadLen = HID_COMMAND_PAYLOAD_LEN;
						}
					} else if (reportId != HID_COMMAND_REPORT_ID) {
						return false;
					}
					if (payloadLen <= HID_COMMAND_PAYLOAD_LEN) {
						if (_cmdCount < HID_COMMAND_QUEUE_SLOTS) {
							memcpy(_cmdRing[_cmdHead], payload, payloadLen);
							_cmdHead = (_cmdHead + 1) % HID_COMMAND_QUEUE_SLOTS;
							_cmdCount++;
						}
						return true;
					}
				}
				return false;
			}
#endif
		}
	}

	return false;
}

HID_::HID_(void) : PluggableUSBModule(
#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT
    2,
#else
    1,
#endif
    1, epType),
                   rootNode(NULL), descriptorSize(0),
                   protocol(HID_REPORT_PROTOCOL), idle(1)
{
	epType[0] = EP_TYPE_INTERRUPT_IN;
#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT
	epType[1] = EP_TYPE_INTERRUPT_OUT;
#endif
#if HID_COMMAND_ENABLED
	_cmdHead = 0;
	_cmdTail = 0;
	_cmdCount = 0;
#endif
	PluggableUSB().plug(this);
}

int HID_::begin(void)
{
	return 0;
}

#if HID_COMMAND_ENABLED

int HID_::availableReportPackets(void)
{
	return _cmdCount;
}

int HID_::readReportPacket(uint8_t* dst, uint8_t maxlen)
{
	if (_cmdCount == 0)
		return 0;
	uint8_t len = HID_COMMAND_PAYLOAD_LEN;
	if (len > maxlen)
		len = maxlen;
	memcpy(dst, _cmdRing[_cmdTail], len);
	_cmdTail = (_cmdTail + 1) % HID_COMMAND_QUEUE_SLOTS;
	_cmdCount--;
	return len;
}

#endif

#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT

int HID_::availableOutReport(void)
{
	return USB_Available(pluggedEndpoint + 1);
}

int HID_::readOutReport(uint8_t* dst, uint8_t maxlen)
{
	uint8_t scratch[HID_COMMAND_TOTAL_LEN];
	int n = USB_Recv(pluggedEndpoint + 1, scratch, HID_COMMAND_TOTAL_LEN);
	if (n <= 0)
		return 0;
	if (scratch[0] != HID_COMMAND_REPORT_ID)
		return 0;
	uint8_t payloadLen = (uint8_t)n - 1;
	if (payloadLen > HID_COMMAND_PAYLOAD_LEN)
		payloadLen = HID_COMMAND_PAYLOAD_LEN;
	if (payloadLen > maxlen)
		payloadLen = maxlen;
	memcpy(dst, scratch + 1, payloadLen);
	return payloadLen;
}

#endif

#endif /* if defined(USBCON) */
