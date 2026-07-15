#include "BlueprintContextSha256.h"

#include "HAL/FileManager.h"
#include "Serialization/Archive.h"

namespace BlueprintContextSha256Private
{
	constexpr uint32 RoundConstants[64] =
	{
		0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u,
		0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
		0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
		0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
		0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu,
		0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
		0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
		0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
		0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
		0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
		0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u,
		0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
		0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u,
		0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
		0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
		0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u
	};

	FORCEINLINE uint32 RotateRight(const uint32 Value, const uint32 Count)
	{
		return (Value >> Count) | (Value << (32u - Count));
	}

	class FSha256Hasher
	{
	public:
		FSha256Hasher()
		{
			State[0] = 0x6a09e667u;
			State[1] = 0xbb67ae85u;
			State[2] = 0x3c6ef372u;
			State[3] = 0xa54ff53au;
			State[4] = 0x510e527fu;
			State[5] = 0x9b05688cu;
			State[6] = 0x1f83d9abu;
			State[7] = 0x5be0cd19u;
		}

		void Update(const uint8* Data, int64 Length)
		{
			if (!Data || Length <= 0)
			{
				return;
			}

			TotalBytes += static_cast<uint64>(Length);
			while (Length > 0)
			{
				const uint32 CopyLength = static_cast<uint32>(FMath::Min<int64>(Length, 64 - BufferSize));
				FMemory::Memcpy(Buffer + BufferSize, Data, CopyLength);
				BufferSize += CopyLength;
				Data += CopyLength;
				Length -= CopyLength;

				if (BufferSize == 64)
				{
					Transform(Buffer);
					BufferSize = 0;
				}
			}
		}

		void Final(uint8 OutDigest[32])
		{
			const uint64 MessageBitLength = TotalBytes * 8u;
			uint8 Padding[128]{};
			Padding[0] = 0x80u;

			const uint32 PaddingLength = BufferSize < 56u
				? 56u - BufferSize
				: 120u - BufferSize;
			Update(Padding, PaddingLength);

			uint8 EncodedLength[8];
			for (int32 Index = 0; Index < 8; ++Index)
			{
				EncodedLength[7 - Index] = static_cast<uint8>(MessageBitLength >> (Index * 8));
			}
			Update(EncodedLength, 8);

			check(BufferSize == 0);
			for (int32 StateIndex = 0; StateIndex < 8; ++StateIndex)
			{
				OutDigest[StateIndex * 4 + 0] = static_cast<uint8>(State[StateIndex] >> 24);
				OutDigest[StateIndex * 4 + 1] = static_cast<uint8>(State[StateIndex] >> 16);
				OutDigest[StateIndex * 4 + 2] = static_cast<uint8>(State[StateIndex] >> 8);
				OutDigest[StateIndex * 4 + 3] = static_cast<uint8>(State[StateIndex]);
			}
		}

	private:
		void Transform(const uint8 Block[64])
		{
			uint32 Words[64];
			for (int32 Index = 0; Index < 16; ++Index)
			{
				const int32 Offset = Index * 4;
				Words[Index] =
					(static_cast<uint32>(Block[Offset + 0]) << 24)
					| (static_cast<uint32>(Block[Offset + 1]) << 16)
					| (static_cast<uint32>(Block[Offset + 2]) << 8)
					| static_cast<uint32>(Block[Offset + 3]);
			}

			for (int32 Index = 16; Index < 64; ++Index)
			{
				const uint32 S0 = RotateRight(Words[Index - 15], 7)
					^ RotateRight(Words[Index - 15], 18)
					^ (Words[Index - 15] >> 3);
				const uint32 S1 = RotateRight(Words[Index - 2], 17)
					^ RotateRight(Words[Index - 2], 19)
					^ (Words[Index - 2] >> 10);
				Words[Index] = Words[Index - 16] + S0 + Words[Index - 7] + S1;
			}

			uint32 A = State[0];
			uint32 B = State[1];
			uint32 C = State[2];
			uint32 D = State[3];
			uint32 E = State[4];
			uint32 F = State[5];
			uint32 G = State[6];
			uint32 H = State[7];

			for (int32 Index = 0; Index < 64; ++Index)
			{
				const uint32 Sigma1 = RotateRight(E, 6) ^ RotateRight(E, 11) ^ RotateRight(E, 25);
				const uint32 Choice = (E & F) ^ ((~E) & G);
				const uint32 Temp1 = H + Sigma1 + Choice + RoundConstants[Index] + Words[Index];
				const uint32 Sigma0 = RotateRight(A, 2) ^ RotateRight(A, 13) ^ RotateRight(A, 22);
				const uint32 Majority = (A & B) ^ (A & C) ^ (B & C);
				const uint32 Temp2 = Sigma0 + Majority;

				H = G;
				G = F;
				F = E;
				E = D + Temp1;
				D = C;
				C = B;
				B = A;
				A = Temp1 + Temp2;
			}

			State[0] += A;
			State[1] += B;
			State[2] += C;
			State[3] += D;
			State[4] += E;
			State[5] += F;
			State[6] += G;
			State[7] += H;
		}

		uint32 State[8]{};
		uint64 TotalBytes = 0;
		uint8 Buffer[64]{};
		uint32 BufferSize = 0;
	};

	FString DigestToHex(const uint8 Digest[32])
	{
		constexpr TCHAR HexDigits[] = TEXT("0123456789abcdef");
		FString Result;
		Result.Reserve(64);
		for (int32 Index = 0; Index < 32; ++Index)
		{
			Result.AppendChar(HexDigits[(Digest[Index] >> 4) & 0x0f]);
			Result.AppendChar(HexDigits[Digest[Index] & 0x0f]);
		}
		return Result;
	}
}

bool FBlueprintContextSha256::HashFile(const FString& Filename, FString& OutHexDigest)
{
	using namespace BlueprintContextSha256Private;

	OutHexDigest.Reset();
	TUniquePtr<FArchive> Reader(IFileManager::Get().CreateFileReader(*Filename));
	if (!Reader)
	{
		return false;
	}

	FSha256Hasher Hasher;
	TArray<uint8> ReadBuffer;
	ReadBuffer.SetNumUninitialized(1024 * 1024);

	int64 Remaining = Reader->TotalSize();
	while (Remaining > 0)
	{
		const int64 ReadLength = FMath::Min<int64>(Remaining, ReadBuffer.Num());
		Reader->Serialize(ReadBuffer.GetData(), ReadLength);
		if (Reader->IsError())
		{
			return false;
		}
		Hasher.Update(ReadBuffer.GetData(), ReadLength);
		Remaining -= ReadLength;
	}

	uint8 Digest[32];
	Hasher.Final(Digest);
	OutHexDigest = DigestToHex(Digest);
	return true;
}
