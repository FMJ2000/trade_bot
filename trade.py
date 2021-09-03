# Martin Hanekom
# BTC Trade Bot

import os, asyncio, sys
from re import M
from binance import AsyncClient, BinanceSocketManager
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException

min_val = 0.01
max_val = 0.05

class Bot:
	def __init__(self, real, symbol):
		self.output = {'streams': {}, 'log': ''}
		self.user = {}
		self.real = real
		self.symbol = symbol
		self.quantity = min_val
		self.stop_loss = 20.0
		self.take_profit = 100.0
		self.adjust = 10.0
		self.buy_int = 5
		self.price = 0
		self.changing_orders = False
		self.win_n = 0
		self.lose_n = 0
		self.prev_buy_price = 0.0
		self.num_orders = 0
		
	async def initClient(self):
		if self.real:
			self.api_key = os.environ.get('binance_api')
			self.api_secret = os.environ.get('binance_secret')
			self.client = await AsyncClient.create(api_key=self.api_key, api_secret=self.api_secret, testnet=False)
		else:
			self.api_key = os.environ.get('binance_api_demo')
			self.api_secret = os.environ.get('binance_secret_demo')
			self.client = await AsyncClient.create(api_key=self.api_key, api_secret=self.api_secret, testnet=True)
			self.client.API_URL = 'https://testnet.binance.vision/api'

		self.bm = BinanceSocketManager(self.client)

	async def getUser(self):
		self.user = await self.client.get_account()
		self.orders = await self.client.get_open_orders()
		self.num_orders = len(self.orders)
		self.printOutput()

	async def buy_stop_limit(self):
		try:
			for asset in self.user['balances']:
				if asset['asset'] == 'BTC':
					if float(asset['free']) < 1.0 - min_val:
						self.quantity = round(1.0 - float(asset['free']), 3)
						if self.quantity > max_val:
							self.quantity = max_val
					else:
						self.quantity = min_val
			buy_order = await self.client.order_market_buy(
				symbol=self.symbol,
				quantity=self.quantity
			)
			self.price = float(buy_order['fills'][0]['price'])
			self.prev_buy_price = self.price
			self.output['log'] += f"Buy order: {round(float(buy_order['fills'][0]['qty']), 4)} @ {round(float(buy_order['fills'][0]['price']), 4)} \n"
			await self.sell_stop_limit()
		except BinanceAPIException as e:
			self.output['log'] += str(e) + '\n'
			return
		except BinanceOrderException as e:
			self.output['log'] += str(e) + '\n'
			return

	async def sell_stop_limit(self):
		try:
			sell_limit = await self.client.order_oco_sell(
				symbol=self.symbol,
				quantity=self.quantity,
				price=str(round(self.price + self.take_profit)),
				stopPrice=str(round(self.price - self.stop_loss)),
				stopLimitPrice=str(round(self.price - 2 * self.stop_loss)),
				stopLimitTimeInForce=Client.TIME_IN_FORCE_GTC
			)
			for order in sell_limit['orderReports']:
				self.output['log'] += f"Sell order: {round(float(order['origQty']), 4)} @ {round(float(order['price']), 4)}"
				if 'btcusdt@trade' in self.output['streams']:
					self.output['log'] += f" | {round(float(self.output['streams']['btcusdt@trade']['p']), 4)} {self.symbol}\n"
				else:
					self.output['log'] += '\n'
			for asset in self.user['balances']:
				if asset['asset'] == 'BTC':
					if float(asset['free']) > 1.0 + min_val:
						self.quantity = round(float(asset['free']) - 1.0, 3)
						if self.quantity > max_val:
							self.quantity = max_val
					else:
						self.quantity = min_val
			return 0
		except BinanceAPIException as e:
			self.output['log'] += str(e) + '\n'
			return 1
		except BinanceOrderException as e:
			self.output['log'] += str(e) + '\n'
			return 1

	async def cancel_orders(self, ids):
		for id in ids:
			try:
				cancel = await self.client.cancel_order(
					symbol=self.symbol,
					orderId=id
				)
				self.output['log'] += 'Cancelling orders\n'
			except BinanceAPIException as e:
				self.output['log'] += str(e) + '\n'
			except BinanceOrderException as e:
				self.output['log'] += str(e) + '\n'
	
	async def socket_start(self):
		f_success = False
		while not f_success:
			try:
				await self.getUser()
				f_success = True
				self.output['log'] += 'Websocket init successful...\n'
				await asyncio.gather(
					self.trade(),
					self.read_trade(),
					self.read_user()
				)
				await self.client.close_connection()
			except BinanceAPIException as e:
				self.output['log'] += str(e) + '\n'
			except BinanceOrderException as e:
				self.output['log'] += str(e) + '\n'
			self.output['log'] += 'Websocket init failed...\n'
				
	async def getOrders(self):
		if self.changing_orders:
			return
		if self.num_orders == 0:
			await self.buy_stop_limit()

	async def trade(self):
		while True:
			await self.getOrders()
			await self.getUser()
			await asyncio.sleep(2)

	async def read_trade(self):
		ts = self.bm.multiplex_socket(['btcusdt@trade', 'ethusdt@trade', 'adausdt@trade', 'bnbusdt@trade'])

		async with ts as tscm:
			while True:
				res = await tscm.recv()
				if self.num_orders > 0 and res['data']['s'] == self.symbol and float(res['data']['p']) >= self.price + self.adjust:
					self.changing_orders = True
					old_orders = []
					for order in self.orders:
						old_orders.append(int(order['orderId']))
					success = 1
					while success != 0:
						self.price = float(res['data']['p'])
						success = await self.sell_stop_limit()
					await self.cancel_orders(old_orders)
					self.changing_orders = False
				self.output['streams'][res['stream']] = res['data']
				self.printOutput()

	async def read_user(self):
		ts = self.bm.user_socket()

		async with ts as tscm:
			while True:
				res = await tscm.recv()
				self.executionReport(res)

	def executionReport(self, res):
		if res['e'] == 'listStatus':
			self.output['log'] += f"List update: {len(res['O'])} orders\n"
			self.num_orders = len(res['O'])
		elif res['e'] == 'executionReport' and res['s'] == self.symbol and res['x'] == 'TRADE':
			self.output['log'] += f"Execution update: {round(float(res['q']), 4)} "
			if res['S'] == 'SELL':
				self.output['log'] += f"sold @ {round(float(res['p']), 4)} "
				if float(res['p']) > self.prev_buy_price:
					self.output['log'] += "(Gain)"
					self.win_n += 1
				else:
					self.output['log'] += "(Loss)"
					self.lose_n += 1
			elif res['S'] == 'BUY':
				self.output['log'] += f"bought @ {round(float(res['p']), 4)} "
			self.output['log'] += '\n'

	def printOutput(self):
		print('\33[2J\33[H')

		print('Account:')
		print(f"Type: {self.user['accountType']} | mc: {float(self.user['makerCommission']) / 1000.0} | tc: {float(self.user['takerCommission']) / 1000.0} | {self.win_n}/{self.lose_n}")
		if len(self.user['balances']) == 0:
			print('account empty')
		else:
			for asset in self.user['balances']:
				print(f"{asset['asset']}: {round(float(asset['free']), 4)}/{round(float(asset['locked']), 4)}", end=' | ')
			print()

		print(f'\nOrders: ({self.num_orders})')
		for order in self.orders:
			print(f"{order['symbol']}: {round(float(order['origQty']), 4)} @ {round(float(order['price']), 4)} ({order['side']}/{order['type']}), id: {order['orderId']}")

		print('\nExchange prices:')
		for stream in self.output['streams'].values():
			print(f"{stream['s']}: {round(float(stream['p']), 4)} ({round(float(stream['q']), 4)})", end=' | ')
		print()

		print('\nLog:\n', end='')
		print(self.output['log'][-1500:])

async def main():
	real = False
	if len(sys.argv) >= 2:
		real = True if sys.argv[1] == 'real' else False
	
	bot = Bot(real, 'BTCUSDT')
	await bot.initClient()
	await bot.socket_start()

if __name__ == '__main__':
	print('Bitcoin Trading Bot\n')
	
	loop = asyncio.get_event_loop()
	loop.run_until_complete(main())
