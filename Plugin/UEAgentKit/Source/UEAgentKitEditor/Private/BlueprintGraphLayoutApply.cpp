#include "BlueprintGraphLayoutApply.h"

#include "BlueprintContextAnalysis.h"
#include "BlueprintEditor.h"
#include "EditorBridgeHandlerUtils.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "Engine/Blueprint.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/SecureHash.h"
#include "ScopedTransaction.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"

#if WITH_DEV_AUTOMATION_TESTS
#include "Misc/AutomationTest.h"
#endif

#define LOCTEXT_NAMESPACE "UEAgentKitGraphLayout"

namespace UEAgentKitBlueprintGraphLayout
{
	namespace
	{
		constexpr int32 MaxMovedPositions = 1000;

		bool IsInt32JsonNumber(const TSharedPtr<FJsonValue>& Value, int32& OutValue);

		bool Fail(FString& OutErrorCode, FString& OutErrorMessage, const TCHAR* Code, const FString& Message)
		{
			OutErrorCode = Code;
			OutErrorMessage = Message;
			return false;
		}

		bool IsCanonicalGuid(const FString& Text, FGuid& OutGuid)
		{
			if (!FGuid::Parse(Text, OutGuid) || !OutGuid.IsValid())
			{
				return false;
			}
			return Text.Equals(OutGuid.ToString(EGuidFormats::DigitsWithHyphensLower), ESearchCase::CaseSensitive);
		}

		bool ParseNodeId(const FString& NodeId, FGuid& OutGuid)
		{
			if (!NodeId.StartsWith(TEXT("n:"), ESearchCase::CaseSensitive))
			{
				return false;
			}
			return IsCanonicalGuid(NodeId.Mid(2), OutGuid);
		}

		bool IsSha256Text(const FString& Value)
		{
			if (Value.Len() != 71 || !Value.StartsWith(TEXT("sha256:"), ESearchCase::CaseSensitive))
			{
				return false;
			}
			for (int32 Index = 7; Index < Value.Len(); ++Index)
			{
				const TCHAR Character = Value[Index];
				if (!((Character >= TEXT('0') && Character <= TEXT('9'))
					|| (Character >= TEXT('a') && Character <= TEXT('f'))))
				{
					return false;
				}
			}
			return true;
		}

		bool IsSha1Text(const FString& Value)
		{
			if (Value.Len() != 45 || !Value.StartsWith(TEXT("sha1:"), ESearchCase::CaseSensitive))
			{
				return false;
			}
			for (int32 Index = 5; Index < Value.Len(); ++Index)
			{
				const TCHAR Character = Value[Index];
				if (!((Character >= TEXT('0') && Character <= TEXT('9'))
					|| (Character >= TEXT('a') && Character <= TEXT('f'))))
				{
					return false;
				}
			}
			return true;
		}

		void AddErrorCode(TArray<FString>& OutErrorCodes, const TCHAR* Code)
		{
			OutErrorCodes.AddUnique(Code);
		}

		bool SameGuidSet(const TSet<FGuid>& Left, const TSet<FGuid>& Right)
		{
			if (Left.Num() != Right.Num())
			{
				return false;
			}
			for (const FGuid& Guid : Left)
			{
				if (!Right.Contains(Guid))
				{
					return false;
				}
			}
			return true;
		}

		bool ContainsAllGuids(const TSet<FGuid>& Container, const TSet<FGuid>& Values)
		{
			for (const FGuid& Guid : Values)
			{
				if (!Container.Contains(Guid))
				{
					return false;
				}
			}
			return true;
		}

		FString HashText(const FString& Text)
		{
			FTCHARToUTF8 Utf8(*Text);
			uint8 Hash[FSHA1::DigestSize];
			FSHA1::HashBuffer(Utf8.Get(), Utf8.Length(), Hash);
			return FString::Printf(TEXT("sha1:%s"), *BytesToHex(Hash, UE_ARRAY_COUNT(Hash)).ToLower());
		}

		uint32 RotateRight32(const uint32 Value, const uint32 Bits)
		{
			return (Value >> Bits) | (Value << (32 - Bits));
		}

		/**
		 * Small, dependency-free SHA-256 used only to verify the canonical
		 * LayoutPlan hash.  UE 5.6 exposes no generally linked FSHA256 class and
		 * FPlatformMisc::GetSHA256Signature is not implemented on Windows, so a
		 * local implementation is the only way to make the cross-language hash a
		 * real runtime gate instead of a format-only check.
		 */
		void Sha256Transform(uint32 State[8], const uint8 Block[64])
		{
			static const uint32 K[64] = {
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

			uint32 W[64];
			for (int32 Index = 0; Index < 16; ++Index)
			{
				W[Index] =
					(static_cast<uint32>(Block[Index * 4]) << 24)
					| (static_cast<uint32>(Block[Index * 4 + 1]) << 16)
					| (static_cast<uint32>(Block[Index * 4 + 2]) << 8)
					| static_cast<uint32>(Block[Index * 4 + 3]);
			}
			for (int32 Index = 16; Index < 64; ++Index)
			{
				const uint32 S0 = RotateRight32(W[Index - 15], 7)
					^ RotateRight32(W[Index - 15], 18)
					^ (W[Index - 15] >> 3);
				const uint32 S1 = RotateRight32(W[Index - 2], 17)
					^ RotateRight32(W[Index - 2], 19)
					^ (W[Index - 2] >> 10);
				W[Index] = W[Index - 16] + S0 + W[Index - 7] + S1;
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
				const uint32 S1 = RotateRight32(E, 6) ^ RotateRight32(E, 11) ^ RotateRight32(E, 25);
				const uint32 Ch = (E & F) ^ ((~E) & G);
				const uint32 Temp1 = H + S1 + Ch + K[Index] + W[Index];
				const uint32 S0 = RotateRight32(A, 2) ^ RotateRight32(A, 13) ^ RotateRight32(A, 22);
				const uint32 Maj = (A & B) ^ (A & C) ^ (B & C);
				const uint32 Temp2 = S0 + Maj;
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

		FString Sha256Hex(const uint8* Data, const int32 Size)
		{
			uint32 State[8] = {
				0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
				0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u
			};
			int32 Offset = 0;
			while (Offset + 64 <= Size)
			{
				Sha256Transform(State, Data + Offset);
				Offset += 64;
			}

			uint8 Tail[128];
			FMemory::Memzero(Tail, sizeof(Tail));
			const int32 Remaining = Size - Offset;
			if (Remaining > 0)
			{
				FMemory::Memcpy(Tail, Data + Offset, Remaining);
			}
			Tail[Remaining] = 0x80;
			const int32 TailBlockCount = Remaining <= 55 ? 1 : 2;
			const int32 TailSize = TailBlockCount * 64;
			const uint64 BitLength = static_cast<uint64>(Size) * 8u;
			for (int32 Index = 0; Index < 8; ++Index)
			{
				Tail[TailSize - 1 - Index] = static_cast<uint8>(BitLength >> (8 * Index));
			}
			Sha256Transform(State, Tail);
			if (TailBlockCount == 2)
			{
				Sha256Transform(State, Tail + 64);
			}

			FString Hex;
			Hex.Reserve(64);
			for (int32 Index = 0; Index < 8; ++Index)
			{
				Hex += FString::Printf(TEXT("%08x"), State[Index]);
			}
			return Hex;
		}

		bool ComputeLayoutHashFromPositions(
			const TArray<TSharedPtr<FJsonValue>>& Positions,
			FString& OutHash)
		{
			struct FLayoutPosition
			{
				FString NodeId;
				int32 X = 0;
				int32 Y = 0;
			};

			TArray<FLayoutPosition> Sorted;
			Sorted.Reserve(Positions.Num());
			for (const TSharedPtr<FJsonValue>& Entry : Positions)
			{
				TSharedPtr<FJsonObject> Item;
				if (Entry.IsValid() && Entry->Type == EJson::Object)
				{
					Item = Entry->AsObject();
				}
				FString NodeId;
				FGuid Guid;
				FLayoutPosition Position;
				if (!Item.IsValid()
					|| !Item->TryGetStringField(TEXT("nodeId"), NodeId)
					|| !ParseNodeId(NodeId, Guid)
					|| !IsInt32JsonNumber(Item->TryGetField(TEXT("x")), Position.X)
					|| !IsInt32JsonNumber(Item->TryGetField(TEXT("y")), Position.Y))
				{
					return false;
				}
				Position.NodeId = NodeId;
				Sorted.Add(MoveTemp(Position));
			}
			Sorted.Sort([](const FLayoutPosition& Left, const FLayoutPosition& Right)
			{
				return Left.NodeId.Compare(Right.NodeId, ESearchCase::CaseSensitive) < 0;
			});

			FString Json = TEXT("[");
			for (int32 Index = 0; Index < Sorted.Num(); ++Index)
			{
				if (Index > 0)
				{
					Json += TEXT(",");
				}
				const FLayoutPosition& Position = Sorted[Index];
				Json += TEXT("{\"nodeId\":\"");
				Json += Position.NodeId;
				Json += TEXT("\",\"x\":");
				Json += FString::FromInt(Position.X);
				Json += TEXT(",\"y\":");
				Json += FString::FromInt(Position.Y);
				Json += TEXT("}");
			}
			Json += TEXT("]");

			const FString Payload = FString(TEXT("ueak.typedgraph.layout/1\n")) + Json;
			FTCHARToUTF8 Utf8(*Payload);
			OutHash = FString(TEXT("sha256:")) + Sha256Hex(
				reinterpret_cast<const uint8*>(Utf8.Get()),
				Utf8.Length());
			return true;
		}

		void AppendField(FString& Builder, const FString& Value)
		{
			Builder.Append(FString::FromInt(Value.Len()));
			Builder.AppendChar(TEXT(':'));
			Builder.Append(Value);
			Builder.AppendChar(TEXT('|'));
		}

		bool IsCommentClass(const UEdGraphNode* Node)
		{
			return Node != nullptr
				&& Node->GetClass() != nullptr
				&& Node->GetClass()->GetPathName().EndsWith(TEXT(".EdGraphNode_Comment"), ESearchCase::CaseSensitive);
		}

		bool IsRerouteClass(const UEdGraphNode* Node)
		{
			return Node != nullptr
				&& Node->GetClass() != nullptr
				&& Node->GetClass()->GetPathName().EndsWith(TEXT(".K2Node_Knot"), ESearchCase::CaseSensitive);
		}

		void CountPinState(
			const UEdGraphNode* Node,
			int32& OutInputPins,
			int32& OutOutputPins,
			int32& OutInputLinks,
			int32& OutOutputLinks)
		{
			OutInputPins = 0;
			OutOutputPins = 0;
			OutInputLinks = 0;
			OutOutputLinks = 0;
			if (Node == nullptr)
			{
				return;
			}
			for (const UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin == nullptr)
				{
					continue;
				}
				if (Pin->Direction == EGPD_Input)
				{
					++OutInputPins;
					OutInputLinks += Pin->LinkedTo.Num();
				}
				else
				{
					++OutOutputPins;
					OutOutputLinks += Pin->LinkedTo.Num();
				}
			}
		}

		FString ComputeGraphFingerprint(UEdGraph* Graph)
		{
			if (Graph == nullptr)
			{
				return FString();
			}

			TArray<UEdGraphNode*> SortedNodes;
			SortedNodes.Reserve(Graph->Nodes.Num());
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node != nullptr)
				{
					SortedNodes.Add(Node);
				}
			}
			SortedNodes.Sort([](const UEdGraphNode& Left, const UEdGraphNode& Right)
			{
				return Left.NodeGuid.ToString(EGuidFormats::Digits) < Right.NodeGuid.ToString(EGuidFormats::Digits);
			});

			FString Builder;
			AppendField(Builder, TEXT("ueak.graph-live-state/1"));
			AppendField(Builder, Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			AppendField(Builder, Graph->GetClass() != nullptr ? Graph->GetClass()->GetPathName() : FString());
			AppendField(Builder, Graph->GetSchema() != nullptr ? Graph->GetSchema()->GetClass()->GetPathName() : FString());

			for (const UEdGraphNode* Node : SortedNodes)
			{
				AppendField(Builder, Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
				AppendField(Builder, Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString());
				AppendField(Builder, FString::FromInt(Node->NodePosX));
				AppendField(Builder, FString::FromInt(Node->NodePosY));
				AppendField(Builder, FString::FromInt(Node->NodeWidth));
				AppendField(Builder, FString::FromInt(Node->NodeHeight));
				AppendField(Builder, Node->NodeComment);
				AppendField(Builder, Node->GetName());

				TArray<UEdGraphPin*> Pins;
				Pins.Reserve(Node->Pins.Num());
				for (UEdGraphPin* Pin : Node->Pins)
				{
					if (Pin != nullptr)
					{
						Pins.Add(Pin);
					}
				}
				Pins.Sort([](const UEdGraphPin& Left, const UEdGraphPin& Right)
				{
					if (Left.PinName != Right.PinName)
					{
						return Left.PinName.LexicalLess(Right.PinName);
					}
					return Left.Direction < Right.Direction;
				});
				AppendField(Builder, FString::FromInt(Pins.Num()));
				for (const UEdGraphPin* Pin : Pins)
				{
					AppendField(Builder, Pin->PinName.ToString());
					AppendField(Builder, Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
					AppendField(Builder, Pin->PinType.PinCategory.ToString());
					AppendField(Builder, Pin->PinType.PinSubCategory.ToString());
					AppendField(Builder, Pin->PinType.PinSubCategoryObject != nullptr
						? Pin->PinType.PinSubCategoryObject->GetPathName()
						: FString());

					TArray<FString> Links;
					Links.Reserve(Pin->LinkedTo.Num());
					for (const UEdGraphPin* Linked : Pin->LinkedTo)
					{
						if (Linked == nullptr || Linked->GetOwningNode() == nullptr)
						{
							Links.Add(TEXT("<null>"));
							continue;
						}
						Links.Add(
							Linked->GetOwningNode()->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)
							+ TEXT("/")
							+ Linked->PinName.ToString());
					}
					Links.Sort();
					AppendField(Builder, FString::FromInt(Links.Num()));
					for (const FString& Link : Links)
					{
						AppendField(Builder, Link);
					}
				}
			}
			return HashText(Builder);
		}

		bool BuildLiveNodeMap(
			UEdGraph* Graph,
			TMap<FGuid, UEdGraphNode*>& OutNodes,
			FString& OutErrorCode,
			FString& OutErrorMessage)
		{
			OutNodes.Reset();
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node == nullptr)
				{
					continue;
				}
				if (!Node->NodeGuid.IsValid())
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-layout-invalid-live-node"),
						TEXT("The live graph contains a node without a valid NodeGuid."));
				}
				if (OutNodes.Contains(Node->NodeGuid))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-layout-duplicate-live-node"),
						TEXT("The live graph contains duplicate NodeGuid values, so no exact apply target can be proven."));
				}
				OutNodes.Add(Node->NodeGuid, Node);
			}
			return true;
		}

		bool ReadRevision(
			UBlueprint* Blueprint,
			FString& OutRevision,
			bool& bOutPackageDirty,
			FString& OutErrorCode,
			FString& OutErrorMessage)
		{
			const TSharedRef<FJsonObject> Revision = FBlueprintContextAnalysis::BuildAssetRevision(Blueprint);
			Revision->TryGetStringField(TEXT("value"), OutRevision);
			Revision->TryGetBoolField(TEXT("packageDirty"), bOutPackageDirty);
			if (!IsSha256Text(OutRevision))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-revision-unavailable"),
					TEXT("The exact package needs a sha256 revision before layout can be applied; guid-only or missing revisions are rejected."));
			}
			return true;
		}

		bool IsInt32JsonNumber(const TSharedPtr<FJsonValue>& Value, int32& OutValue)
		{
			if (!Value.IsValid() || Value->Type != EJson::Number)
			{
				return false;
			}
			const double Number = Value->AsNumber();
			if (!FMath::IsFinite(Number))
			{
				return false;
			}
			if (Number < -2147483648.0 || Number > 2147483647.0)
			{
				return false;
			}
			const int32 Casted = static_cast<int32>(Number);
			if (static_cast<double>(Casted) != Number)
			{
				return false;
			}
			OutValue = Casted;
			return true;
		}

		struct FParsedTarget
		{
			FGuid Guid;
			int32 X = 0;
			int32 Y = 0;
		};

		constexpr int32 MaxKnotChain = 64;

		// A stable, human-readable key for the exact pin type.  The client gate
		// compares the same string carried by the preflight evidence; the live
		// check below recomputes it from the UObject, so a forged preflight
		// cannot widen the write.
		FString PinTypeKey(const UEdGraphPin* Pin)
		{
			if (Pin == nullptr)
			{
				return FString();
			}
			const FEdGraphPinType& Type = Pin->PinType;
			FString Key = Type.PinCategory.ToString();
			if (Type.PinSubCategoryObject.IsValid())
			{
				Key += TEXT(":");
				Key += Type.PinSubCategoryObject->GetPathName();
			}
			switch (Type.ContainerType)
			{
			case EPinContainerType::Array:
				Key = TEXT("array:") + Key;
				break;
			case EPinContainerType::Set:
				Key = TEXT("set:") + Key;
				break;
			case EPinContainerType::Map:
				Key = TEXT("map:") + Key;
				break;
			default:
				break;
			}
			return Key;
		}

		bool GetKnotPins(const UEdGraphNode* Node, const UEdGraphPin*& OutInput, const UEdGraphPin*& OutOutput)
		{
			OutInput = nullptr;
			OutOutput = nullptr;
			if (Node == nullptr)
			{
				return false;
			}
			for (const UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin == nullptr)
				{
					continue;
				}
				if (Pin->Direction == EGPD_Input)
				{
					if (OutInput != nullptr)
					{
						return false;
					}
					OutInput = Pin;
				}
				else
				{
					if (OutOutput != nullptr)
					{
						return false;
					}
					OutOutput = Pin;
				}
			}
			return OutInput != nullptr && OutOutput != nullptr;
		}

		int32 CountKnotTerminals(
			const UEdGraphNode* Start,
			bool bFollowInputs,
			const TMap<FGuid, UEdGraphNode*>& Nodes,
			FString& OutWhy)
		{
			TSet<FGuid> Visited;
			TSet<FGuid> Terminals;
			TArray<const UEdGraphNode*> Stack;
			Stack.Add(Start);
			while (Stack.Num() > 0)
			{
				const UEdGraphNode* Current = Stack.Pop();
				if (Current == nullptr)
				{
					OutWhy = TEXT("contains a missing node");
					return INDEX_NONE;
				}
				if (Visited.Contains(Current->NodeGuid))
				{
					// Revisits are skipped, not treated as a cycle: a legal
					// diamond may reach the same knot twice.  A pure cycle
					// then yields zero terminals and still fails the shape rule.
					continue;
				}
				Visited.Add(Current->NodeGuid);
				if (Visited.Num() > MaxKnotChain)
				{
					OutWhy = TEXT("has a reroute chain longer than the bounded evidence walk");
					return INDEX_NONE;
				}
				const UEdGraphPin* Input = nullptr;
				const UEdGraphPin* Output = nullptr;
				if (!GetKnotPins(Current, Input, Output))
				{
					OutWhy = TEXT("contains a knot without exactly two pins");
					return INDEX_NONE;
				}
				const UEdGraphPin* Side = bFollowInputs ? Input : Output;
				if (Side->LinkedTo.Num() == 0)
				{
					OutWhy = bFollowInputs
						? TEXT("has a dangling knot with no external input")
						: TEXT("has a dangling knot with no external output");
					return INDEX_NONE;
				}
				for (const UEdGraphPin* Linked : Side->LinkedTo)
				{
					const UEdGraphNode* Peer = Linked != nullptr ? Linked->GetOwningNodeUnchecked() : nullptr;
					if (Peer == nullptr)
					{
						OutWhy = TEXT("links to a missing node");
						return INDEX_NONE;
					}
					if (!Nodes.Contains(Peer->NodeGuid))
					{
						OutWhy = TEXT("links to a node outside the graph");
						return INDEX_NONE;
					}
					if (IsRerouteClass(Peer))
					{
						Stack.Add(Peer);
					}
					else
					{
						Terminals.Add(Peer->NodeGuid);
					}
				}
			}
			return Terminals.Num();
		}

		// Legal shapes: the historical one-in/one-out knot, a data fan-out
		// (exactly one external source, at least one sink) and an exec merge
		// (at least one source, exactly one sink).  Pin count, pin direction,
		// exact type equality and chain termination are all required.
		bool ValidateMovedKnot(
			const UEdGraphNode* Node,
			const TMap<FGuid, UEdGraphNode*>& Nodes,
			FString& OutWhy)
		{
			const UEdGraphPin* Input = nullptr;
			const UEdGraphPin* Output = nullptr;
			if (!GetKnotPins(Node, Input, Output))
			{
				OutWhy = TEXT("does not have exactly one input and one output pin");
				return false;
			}
			const int32 InputLinks = Input->LinkedTo.Num();
			const int32 OutputLinks = Output->LinkedTo.Num();
			if (InputLinks == 1 && OutputLinks == 1)
			{
				return true;
			}
			if (InputLinks < 1 || OutputLinks < 1)
			{
				OutWhy = TEXT("is a dangling knot with a missing link");
				return false;
			}
			const FString InputType = PinTypeKey(Input);
			const FString OutputType = PinTypeKey(Output);
			if (InputType.IsEmpty() || !InputType.Equals(OutputType, ESearchCase::CaseSensitive))
			{
				OutWhy = TEXT("has mismatched input/output pin types");
				return false;
			}
			if (InputType.Contains(TEXT("wildcard"), ESearchCase::IgnoreCase))
			{
				OutWhy = TEXT("has an unresolved wildcard pin type");
				return false;
			}
			const int32 Sources = CountKnotTerminals(Node, true, Nodes, OutWhy);
			if (Sources == INDEX_NONE)
			{
				return false;
			}
			const int32 Sinks = CountKnotTerminals(Node, false, Nodes, OutWhy);
			if (Sinks == INDEX_NONE)
			{
				return false;
			}
			if (InputType == TEXT("exec"))
			{
				if (Sources < 1 || Sinks != 1)
				{
					OutWhy = TEXT("must merge at least one exec source into exactly one sink");
					return false;
				}
			}
			else if (Sources != 1 || Sinks < 1)
			{
				OutWhy = TEXT("must fan one data source out to at least one sink");
				return false;
			}
			return true;
		}
	}

	bool ValidatePlanShape(const TSharedPtr<FJsonObject>& Plan, TArray<FString>& OutErrorCodes)
	{
		OutErrorCodes.Reset();
		if (!Plan.IsValid())
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
			return false;
		}

		FString SchemaVersion;
		FString Status;
		FString ErrorCode;
		FString PlanId;
		FString BaseGraphHash;
		FString LayoutHash;
		FString Mode;
		FString EvidenceLevel;
		FString RequiredEvidence;
		bool bAcceptanceEligible = true;
		if (!Plan->TryGetStringField(TEXT("schemaVersion"), SchemaVersion)
			|| !SchemaVersion.StartsWith(TEXT("ueak.layoutplan/1."), ESearchCase::CaseSensitive))
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
		}
		if (!Plan->TryGetStringField(TEXT("status"), Status) || !Status.Equals(TEXT("ok"), ESearchCase::CaseSensitive))
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-plan-rejected"));
		}
		if (!Plan->TryGetStringField(TEXT("errorCode"), ErrorCode) || !ErrorCode.IsEmpty())
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-plan-rejected"));
		}
		if (!Plan->TryGetStringField(TEXT("planId"), PlanId) || PlanId.IsEmpty())
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
		}
		if (!Plan->TryGetStringField(TEXT("baseGraphHash"), BaseGraphHash) || !IsSha256Text(BaseGraphHash))
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
		}
		if (!Plan->TryGetStringField(TEXT("layoutHash"), LayoutHash) || !IsSha256Text(LayoutHash))
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
		}
		if (!Plan->TryGetStringField(TEXT("mode"), Mode)
			|| (!Mode.Equals(TEXT("full"), ESearchCase::CaseSensitive)
				&& !Mode.Equals(TEXT("selection"), ESearchCase::CaseSensitive)
				&& !Mode.Equals(TEXT("incremental"), ESearchCase::CaseSensitive)))
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
		}
		if (!Plan->TryGetStringField(TEXT("evidenceLevel"), EvidenceLevel)
			|| !EvidenceLevel.Equals(TEXT("same-process"), ESearchCase::CaseSensitive)
			|| !Plan->TryGetBoolField(TEXT("acceptanceEligible"), bAcceptanceEligible)
			|| bAcceptanceEligible
			|| !Plan->TryGetStringField(TEXT("requiredAcceptanceEvidence"), RequiredEvidence)
			|| !RequiredEvidence.Equals(TEXT("independent-reload"), ESearchCase::CaseSensitive))
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-evidence-invalid"));
		}

		const TArray<TSharedPtr<FJsonValue>>* Positions = nullptr;
		if (!Plan->TryGetArrayField(TEXT("positions"), Positions) || Positions == nullptr || Positions->IsEmpty())
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
			return OutErrorCodes.IsEmpty();
		}

		TSet<FGuid> PositionGuids;
		for (int32 Index = 0; Index < Positions->Num(); ++Index)
		{
			TSharedPtr<FJsonObject> Position;
			if ((*Positions)[Index].IsValid() && (*Positions)[Index]->Type == EJson::Object)
			{
				Position = (*Positions)[Index]->AsObject();
			}
			if (!Position.IsValid())
			{
				AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
				continue;
			}
			FString NodeId;
			FGuid Guid;
			if (!Position->TryGetStringField(TEXT("nodeId"), NodeId) || !ParseNodeId(NodeId, Guid))
			{
				AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-node-id"));
				continue;
			}
			if (PositionGuids.Contains(Guid))
			{
				AddErrorCode(OutErrorCodes, TEXT("graph-layout-duplicate-node"));
				continue;
			}
			PositionGuids.Add(Guid);

			int32 X = 0;
			int32 Y = 0;
			if (!IsInt32JsonNumber(Position->TryGetField(TEXT("x")), X)
				|| !IsInt32JsonNumber(Position->TryGetField(TEXT("y")), Y))
			{
				AddErrorCode(OutErrorCodes, TEXT("graph-layout-coordinate-out-of-range"));
			}

			const TSharedPtr<FJsonObject>* Size = nullptr;
			if (!Position->TryGetObjectField(TEXT("size"), Size) || Size == nullptr || !Size->IsValid())
			{
				AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
			}
			else
			{
				int32 Width = 0;
				int32 Height = 0;
				FString Source;
				const bool bKnownSource = (*Size)->TryGetStringField(TEXT("source"), Source)
					&& (Source.Equals(TEXT("measured"), ESearchCase::CaseSensitive)
						|| Source.Equals(TEXT("stored"), ESearchCase::CaseSensitive)
						|| Source.Equals(TEXT("estimated"), ESearchCase::CaseSensitive)
						|| Source.Equals(TEXT("unknown"), ESearchCase::CaseSensitive));
				if (!IsInt32JsonNumber((*Size)->TryGetField(TEXT("width")), Width)
					|| !IsInt32JsonNumber((*Size)->TryGetField(TEXT("height")), Height)
					|| Width < 0
					|| Height < 0
					|| !bKnownSource)
				{
					AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
				}
			}
		}

		FString RecomputedLayoutHash;
		if (!ComputeLayoutHashFromPositions(*Positions, RecomputedLayoutHash))
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
		}
		else if (!LayoutHash.Equals(RecomputedLayoutHash, ESearchCase::CaseSensitive))
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-layout-hash-mismatch"));
		}

		const TArray<TSharedPtr<FJsonValue>>* Moved = nullptr;
		if (!Plan->TryGetArrayField(TEXT("movedNodeIds"), Moved) || Moved == nullptr)
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
			return false;
		}
		TSet<FGuid> MovedGuids;
		for (const TSharedPtr<FJsonValue>& Entry : *Moved)
		{
			FGuid Guid;
			if (!Entry.IsValid() || Entry->Type != EJson::String || !ParseNodeId(Entry->AsString(), Guid))
			{
				AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-node-id"));
				continue;
			}
			if (MovedGuids.Contains(Guid))
			{
				AddErrorCode(OutErrorCodes, TEXT("graph-layout-duplicate-node"));
				continue;
			}
			MovedGuids.Add(Guid);
		}
		if (MovedGuids.Num() > MaxMovedPositions)
		{
			AddErrorCode(OutErrorCodes, TEXT("graph-layout-limit-exceeded"));
		}

		for (const TCHAR* FieldName : {TEXT("scopeNodeIds"), TEXT("fixedNodeIds")})
		{
			const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
			if (!Plan->TryGetArrayField(FieldName, Values) || Values == nullptr)
			{
				AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-plan"));
				continue;
			}
			TSet<FGuid> Seen;
			for (const TSharedPtr<FJsonValue>& Entry : *Values)
			{
				FGuid Guid;
				if (!Entry.IsValid() || Entry->Type != EJson::String || !ParseNodeId(Entry->AsString(), Guid))
				{
					AddErrorCode(OutErrorCodes, TEXT("graph-layout-invalid-node-id"));
					continue;
				}
				if (Seen.Contains(Guid))
				{
					AddErrorCode(OutErrorCodes, TEXT("graph-layout-duplicate-node"));
				}
				Seen.Add(Guid);
			}
		}

		return OutErrorCodes.IsEmpty();
	}

	bool ComputeLayoutHash(
		const TSharedPtr<FJsonObject>& Plan,
		FString& OutHash)
	{
		OutHash.Reset();
		const TArray<TSharedPtr<FJsonValue>>* Positions = nullptr;
		if (!Plan.IsValid()
			|| !Plan->TryGetArrayField(TEXT("positions"), Positions)
			|| Positions == nullptr
			|| Positions->IsEmpty())
		{
			return false;
		}
		return ComputeLayoutHashFromPositions(*Positions, OutHash);
	}

	void CollectBlueprintGraphs(UBlueprint* Blueprint, TArray<UEdGraph*>& OutGraphs)
	{
		OutGraphs.Reset();
		if (Blueprint == nullptr)
		{
			return;
		}
		auto AddGraphs = [&OutGraphs](const TArray<TObjectPtr<UEdGraph>>& SourceGraphs)
		{
			for (UEdGraph* Candidate : SourceGraphs)
			{
				if (Candidate != nullptr)
				{
					OutGraphs.Add(Candidate);
				}
			}
		};
		AddGraphs(Blueprint->UbergraphPages);
		AddGraphs(Blueprint->FunctionGraphs);
		AddGraphs(Blueprint->MacroGraphs);
		AddGraphs(Blueprint->DelegateSignatureGraphs);
	}

	bool IsGraphOwnedByBlueprint(UBlueprint* Blueprint, UEdGraph* Graph)
	{
		if (Blueprint == nullptr || Graph == nullptr)
		{
			return false;
		}
		TArray<UEdGraph*> Graphs;
		CollectBlueprintGraphs(Blueprint, Graphs);
		return Graphs.Contains(Graph);
	}

	bool BuildPreflight(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		TSharedPtr<FJsonObject>& OutPreflight,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Blueprint == nullptr || Graph == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-unavailable"),
				TEXT("The exact Blueprint and graph must both be loaded."));
		}
		if (Graph->GraphGuid.IsValid() == false)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-unavailable"),
				TEXT("The target graph has no valid GraphGuid."));
		}
		if (!IsGraphOwnedByBlueprint(Blueprint, Graph))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-mismatch"),
				TEXT("The graph is not one of the exact Blueprint's graph objects."));
		}

		FString Revision;
		bool bPackageDirty = false;
		if (!ReadRevision(Blueprint, Revision, bPackageDirty, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		UPackage* Package = Blueprint->GetOutermost();
		if (Package == nullptr || !Package->GetName().StartsWith(TEXT("/Game/")) || Package->ContainsMap())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-package-invalid"),
				TEXT("Layout apply accepts only one exact non-map /Game Blueprint package."));
		}

		TSharedRef<FJsonObject> Preflight = MakeShared<FJsonObject>();
		Preflight->SetStringField(TEXT("schemaVersion"), TEXT("ueak.graphlayoutpreflight/1.0"));
		Preflight->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Preflight->SetStringField(TEXT("graphGuid"), Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		Preflight->SetStringField(TEXT("graphClassPath"), Graph->GetClass() != nullptr ? Graph->GetClass()->GetPathName() : FString());
		Preflight->SetStringField(TEXT("schemaClassPath"), Graph->GetSchema() != nullptr ? Graph->GetSchema()->GetClass()->GetPathName() : FString());
		Preflight->SetStringField(TEXT("packageRevision"), Revision);
		Preflight->SetBoolField(TEXT("packageDirty"), bPackageDirty);
		Preflight->SetStringField(TEXT("graphFingerprint"), ComputeGraphFingerprint(Graph));
		Preflight->SetStringField(TEXT("graphFingerprintAlgorithm"), TEXT("sha1-length-prefixed-graph-state-v1"));
		Preflight->SetStringField(TEXT("evidenceLevel"), TEXT("same-process"));
		Preflight->SetBoolField(TEXT("acceptanceEligible"), false);
		Preflight->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));

		TArray<UEdGraphNode*> SortedNodes;
		SortedNodes.Reserve(Graph->Nodes.Num());
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr)
			{
				SortedNodes.Add(Node);
			}
		}
		SortedNodes.Sort([](const UEdGraphNode& Left, const UEdGraphNode& Right)
		{
			return Left.NodeGuid.ToString(EGuidFormats::Digits) < Right.NodeGuid.ToString(EGuidFormats::Digits);
		});

		TArray<TSharedPtr<FJsonValue>> Nodes;
		int32 CommentCount = 0;
		int32 RerouteCount = 0;
		TSet<FGuid> SeenNodeGuids;
		for (const UEdGraphNode* Node : SortedNodes)
		{
			if (!Node->NodeGuid.IsValid())
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-invalid-live-node"),
					TEXT("The live graph contains a node without a valid NodeGuid."));
			}
			if (SeenNodeGuids.Contains(Node->NodeGuid))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-duplicate-live-node"),
					TEXT("The live graph contains duplicate NodeGuid values."));
			}
			SeenNodeGuids.Add(Node->NodeGuid);

			int32 InputPins = 0;
			int32 OutputPins = 0;
			int32 InputLinks = 0;
			int32 OutputLinks = 0;
			CountPinState(Node, InputPins, OutputPins, InputLinks, OutputLinks);

			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeId"), FString(TEXT("n:")) + Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			Item->SetStringField(TEXT("guid"), Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			Item->SetStringField(TEXT("classPath"), Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString());
			Item->SetStringField(TEXT("kind"), IsCommentClass(Node) ? TEXT("comment") : (IsRerouteClass(Node) ? TEXT("reroute") : TEXT("node")));
			Item->SetNumberField(TEXT("x"), Node->NodePosX);
			Item->SetNumberField(TEXT("y"), Node->NodePosY);
			Item->SetNumberField(TEXT("width"), Node->NodeWidth);
			Item->SetNumberField(TEXT("height"), Node->NodeHeight);
			Item->SetNumberField(TEXT("inputPinCount"), InputPins);
			Item->SetNumberField(TEXT("outputPinCount"), OutputPins);
			Item->SetNumberField(TEXT("inputLinkCount"), InputLinks);
			Item->SetNumberField(TEXT("outputLinkCount"), OutputLinks);
			if (IsRerouteClass(Node))
			{
				const UEdGraphPin* KnotInput = nullptr;
				const UEdGraphPin* KnotOutput = nullptr;
				if (GetKnotPins(Node, KnotInput, KnotOutput))
				{
					TSharedRef<FJsonObject> Knot = MakeShared<FJsonObject>();
					Knot->SetStringField(TEXT("inputType"), PinTypeKey(KnotInput));
					Knot->SetStringField(TEXT("outputType"), PinTypeKey(KnotOutput));
					TArray<TSharedPtr<FJsonValue>> Sources;
					for (const UEdGraphPin* Linked : KnotInput->LinkedTo)
					{
						const UEdGraphNode* Peer = Linked != nullptr ? Linked->GetOwningNodeUnchecked() : nullptr;
						if (Peer != nullptr)
						{
							Sources.Add(MakeShared<FJsonValueString>(
								Peer->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)));
						}
					}
					TArray<TSharedPtr<FJsonValue>> Sinks;
					for (const UEdGraphPin* Linked : KnotOutput->LinkedTo)
					{
						const UEdGraphNode* Peer = Linked != nullptr ? Linked->GetOwningNodeUnchecked() : nullptr;
						if (Peer != nullptr)
						{
							Sinks.Add(MakeShared<FJsonValueString>(
								Peer->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)));
						}
					}
					Knot->SetArrayField(TEXT("inputSources"), Sources);
					Knot->SetArrayField(TEXT("outputSinks"), Sinks);
					Item->SetObjectField(TEXT("knot"), Knot);
				}
			}
			Nodes.Add(MakeShared<FJsonValueObject>(Item));

			if (IsCommentClass(Node))
			{
				++CommentCount;
			}
			if (IsRerouteClass(Node))
			{
				++RerouteCount;
			}
		}

		Preflight->SetNumberField(TEXT("nodeCount"), Nodes.Num());
		Preflight->SetNumberField(TEXT("commentCount"), CommentCount);
		Preflight->SetNumberField(TEXT("rerouteCount"), RerouteCount);
		Preflight->SetArrayField(TEXT("nodes"), Nodes);
		OutPreflight = Preflight;
		OutErrorCode.Reset();
		OutErrorMessage.Reset();
		return true;
	}

	bool BuildFocusedEditorPreflight(
		TSharedPtr<FJsonObject>& OutPreflight,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutPreflight.Reset();
		if (GEditor == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-editor-unavailable"),
				TEXT("The Unreal Editor is unavailable."));
		}

		UAssetEditorSubsystem* AssetEditorSubsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
		if (AssetEditorSubsystem == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-editor-unavailable"),
				TEXT("The Asset Editor Subsystem is unavailable."));
		}

		IAssetEditorInstance* SelectedInstance = nullptr;
		double LatestActivation = -1.0;
		for (IAssetEditorInstance* Instance : AssetEditorSubsystem->GetAllOpenEditors())
		{
			if (Instance == nullptr || Instance->GetEditorName() != FName(TEXT("BlueprintEditor")))
			{
				continue;
			}
			const double Activation = Instance->GetLastActivationTime();
			if (SelectedInstance == nullptr || Activation > LatestActivation)
			{
				SelectedInstance = Instance;
				LatestActivation = Activation;
			}
		}
		if (SelectedInstance == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-editor-not-open"),
				TEXT("Open the exact Blueprint in an ordinary Blueprint Editor first."));
		}

		FBlueprintEditor* BlueprintEditor = static_cast<FBlueprintEditor*>(SelectedInstance);
		UBlueprint* Blueprint = BlueprintEditor->GetBlueprintObj();
		if (Blueprint == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-asset-not-loaded"),
				TEXT("The active Blueprint Editor does not own a loaded Blueprint."));
		}
		UEdGraph* Graph = BlueprintEditor->GetFocusedGraph();
		if (Graph == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-not-focused"),
				TEXT("Open and focus the exact target graph before reading its layout preflight."));
		}
		if (!BuildPreflight(Blueprint, Graph, OutPreflight, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		OutPreflight->SetStringField(TEXT("source"), TEXT("focused-blueprint-editor"));
		OutPreflight->SetBoolField(TEXT("readOnly"), true);
		OutPreflight->SetStringField(TEXT("editorName"), SelectedInstance->GetEditorName().ToString());
		OutPreflight->SetStringField(TEXT("blueprintPath"), Blueprint->GetPathName());
		OutPreflight->SetStringField(TEXT("graphName"), Graph->GetName());
		OutPreflight->SetBoolField(TEXT("editable"), BlueprintEditor->IsEditable(Graph));
		OutPreflight->SetNumberField(TEXT("lastActivationTime"), LatestActivation);
		return true;
	}

	bool ResolveExactTarget(
		const FString& AssetPath,
		const FString& GraphGuidText,
		UBlueprint*& OutBlueprint,
		UEdGraph*& OutGraph,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutBlueprint = nullptr;
		OutGraph = nullptr;
		if (!UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(AssetPath))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-invalid-parameters"),
				TEXT("assetPath must be one exact /Game Blueprint Object Path."));
		}
		FGuid GraphGuid;
		if (!IsCanonicalGuid(GraphGuidText, GraphGuid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-invalid-parameters"),
				TEXT("graphGuid must be a canonical lowercase GUID."));
		}

		UObject* Asset = StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false);
		UBlueprint* Blueprint = Cast<UBlueprint>(Asset);
		if (Blueprint == nullptr || !Blueprint->GetPathName().Equals(AssetPath, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-asset-not-loaded"),
				TEXT("The exact Blueprint asset must already be loaded; layout apply never loads or creates assets."));
		}
		UPackage* Package = Blueprint->GetOutermost();
		if (Package == nullptr || !Package->GetName().StartsWith(TEXT("/Game/")) || Package->ContainsMap())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-package-invalid"),
				TEXT("The exact Blueprint must live in one non-map /Game package."));
		}

		TArray<UEdGraph*> Graphs;
		CollectBlueprintGraphs(Blueprint, Graphs);

		int32 MatchCount = 0;
		for (UEdGraph* Candidate : Graphs)
		{
			if (Candidate != nullptr && Candidate->GraphGuid == GraphGuid)
			{
				OutGraph = Candidate;
				++MatchCount;
			}
		}
		if (MatchCount == 0 || OutGraph == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-not-found"),
				TEXT("No graph with the exact GraphGuid exists on the loaded Blueprint."));
		}
		if (MatchCount != 1)
		{
			OutGraph = nullptr;
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-ambiguous"),
				TEXT("More than one graph on the Blueprint has the supplied GraphGuid."));
		}
		OutBlueprint = Blueprint;
		return true;
	}

	bool ApplyToGraph(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TSharedPtr<FJsonObject>& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Blueprint == nullptr || Graph == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-unavailable"),
				TEXT("The exact Blueprint and graph must both be loaded."));
		}
		if (!IsGraphOwnedByBlueprint(Blueprint, Graph))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-mismatch"),
				TEXT("The graph is not one of the exact Blueprint's graph objects."));
		}
		if (!Graph->bEditable)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-not-editable"),
				TEXT("The graph is marked read-only and cannot receive layout positions."));
		}
		if (Cast<UEdGraphSchema_K2>(Graph->GetSchema()) == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-not-k2"),
				TEXT("Layout apply currently accepts only an ordinary K2 Blueprint graph."));
		}
		if (GEditor == nullptr || GEditor->Trans == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-transaction-unavailable"),
				TEXT("The Editor transaction history is unavailable; layout cannot be one Undo/Redo unit."));
		}
		if (GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-pie-active"),
				TEXT("Graph layout apply is unavailable while PIE or SIE is active."));
		}
		if (!GEditor->CanTransact())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-transaction-unavailable"),
				TEXT("The Editor cannot begin a transaction in its current loading/post-load state."));
		}
		if (GEditor->Trans->IsActive())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-transaction-active"),
				TEXT("Another Editor transaction is already active; layout must be its own Undo/Redo unit."));
		}

		const TSharedPtr<FJsonObject>* PlanField = nullptr;
		if (!Request.IsValid() || !Request->TryGetObjectField(TEXT("plan"), PlanField) || PlanField == nullptr || !PlanField->IsValid())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-invalid-parameters"),
				TEXT("The apply request must contain one exact layout plan object."));
		}
		const TSharedPtr<FJsonObject>& Plan = *PlanField;

		FString RequestSchema;
		FString RequestedAssetPath;
		FString RequestedGraphGuid;
		FString RequestedMode;
		FString RequestedEvidence;
		bool bAllowSave = true;
		bool bAllowCompile = true;
		bool bAcceptanceEligible = true;
		if (!Request->TryGetStringField(TEXT("schemaVersion"), RequestSchema)
			|| !RequestSchema.StartsWith(TEXT("ueak.graphlayoutapply/1."), ESearchCase::CaseSensitive)
			|| !Request->TryGetStringField(TEXT("assetPath"), RequestedAssetPath)
			|| !Request->TryGetStringField(TEXT("graphGuid"), RequestedGraphGuid)
			|| !Request->TryGetStringField(TEXT("requestedMode"), RequestedMode)
			|| !RequestedMode.Equals(TEXT("position-only"), ESearchCase::CaseSensitive)
			|| !Request->TryGetBoolField(TEXT("allowSave"), bAllowSave)
			|| bAllowSave
			|| !Request->TryGetBoolField(TEXT("allowCompile"), bAllowCompile)
			|| bAllowCompile
			|| !Request->TryGetBoolField(TEXT("acceptanceEligible"), bAcceptanceEligible)
			|| bAcceptanceEligible
			|| !Request->TryGetStringField(TEXT("requiredAcceptanceEvidence"), RequestedEvidence)
			|| !RequestedEvidence.Equals(TEXT("independent-reload"), ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-invalid-parameters"),
				TEXT("The request must be schema 1.x position-only, must not allow Save or Compile, and must require independent reload."));
		}
		if (!RequestedAssetPath.Equals(Blueprint->GetPathName(), ESearchCase::CaseSensitive)
			|| !RequestedGraphGuid.Equals(Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower), ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-mismatch"),
				TEXT("The request assetPath/graphGuid do not match the exact resolved Blueprint and graph."));
		}

		TArray<FString> ShapeErrors;
		if (!ValidatePlanShape(Plan, ShapeErrors))
		{
			OutErrorCode = ShapeErrors.IsEmpty() ? TEXT("graph-layout-invalid-plan") : ShapeErrors[0];
			OutErrorMessage = FString::Printf(
				TEXT("The LayoutPlan failed shape validation: %s."),
				*FString::Join(ShapeErrors, TEXT(", ")));
			return false;
		}

		UPackage* Package = Blueprint->GetOutermost();
		if (Package == nullptr || Package->IsDirty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-package-dirty"),
				TEXT("Save or revert the exact Blueprint package before applying graph layout; this handler does not chain dirty transactions."));
		}

		FString ExpectedRevision;
		FString ExpectedFingerprint;
		if (!Request->TryGetStringField(TEXT("expectedPackageRevision"), ExpectedRevision)
			|| !IsSha256Text(ExpectedRevision))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-invalid-parameters"),
				TEXT("expectedPackageRevision must be one exact sha256 revision."));
		}
		if (!Request->TryGetStringField(TEXT("expectedGraphFingerprint"), ExpectedFingerprint)
			|| !IsSha1Text(ExpectedFingerprint))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-invalid-parameters"),
				TEXT("expectedGraphFingerprint must be the exact live graph fingerprint from preflight."));
		}

		FString CurrentRevision;
		bool bPackageDirty = false;
		if (!ReadRevision(Blueprint, CurrentRevision, bPackageDirty, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		if (bPackageDirty || !CurrentRevision.Equals(ExpectedRevision, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-revision-conflict"),
				FString::Printf(TEXT("Package revision changed. Expected=%s Current=%s Dirty=%s"),
					*ExpectedRevision,
					*CurrentRevision,
					bPackageDirty ? TEXT("true") : TEXT("false")));
		}

		const FString CurrentFingerprint = ComputeGraphFingerprint(Graph);
		if (!CurrentFingerprint.Equals(ExpectedFingerprint, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-fingerprint-conflict"),
				TEXT("The live graph changed after preflight; re-export and re-plan before applying."));
		}

		TMap<FGuid, UEdGraphNode*> Nodes;
		if (!BuildLiveNodeMap(Graph, Nodes, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		const TArray<TSharedPtr<FJsonValue>>* Positions = nullptr;
		if (!Plan->TryGetArrayField(TEXT("positions"), Positions) || Positions == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-invalid-plan"),
				TEXT("The LayoutPlan has no positions array."));
		}
		if (Positions->Num() != Nodes.Num())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-node-missing"),
				TEXT("The LayoutPlan must contain exactly one position for every live graph node."));
		}

		TMap<FGuid, FIntPoint> Targets;
		for (const TSharedPtr<FJsonValue>& Entry : *Positions)
		{
			TSharedPtr<FJsonObject> Position;
			if (Entry.IsValid() && Entry->Type == EJson::Object)
			{
				Position = Entry->AsObject();
			}
			FString NodeId;
			FGuid Guid;
			int32 X = 0;
			int32 Y = 0;
			if (!Position.IsValid()
				|| !Position->TryGetStringField(TEXT("nodeId"), NodeId)
				|| !ParseNodeId(NodeId, Guid)
				|| !IsInt32JsonNumber(Position->TryGetField(TEXT("x")), X)
				|| !IsInt32JsonNumber(Position->TryGetField(TEXT("y")), Y))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-invalid-plan"),
					TEXT("A position entry is missing its exact nodeId/x/y."));
			}
			UEdGraphNode* const* LiveNode = Nodes.Find(Guid);
			if (LiveNode == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-node-unknown"),
					TEXT("The LayoutPlan contains a node GUID that does not exist on the live graph."));
			}
			Targets.Add(Guid, FIntPoint(X, Y));
		}
		if (Targets.Num() != Nodes.Num())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-node-missing"),
				TEXT("The LayoutPlan does not cover every live graph node exactly once."));
		}

		const TArray<TSharedPtr<FJsonValue>>* Moved = nullptr;
		const TArray<TSharedPtr<FJsonValue>>* Scope = nullptr;
		const TArray<TSharedPtr<FJsonValue>>* Fixed = nullptr;
		if (!Plan->TryGetArrayField(TEXT("movedNodeIds"), Moved)
			|| !Plan->TryGetArrayField(TEXT("scopeNodeIds"), Scope)
			|| !Plan->TryGetArrayField(TEXT("fixedNodeIds"), Fixed)
			|| Moved == nullptr
			|| Scope == nullptr
			|| Fixed == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-invalid-plan"),
				TEXT("The LayoutPlan is missing movedNodeIds, scopeNodeIds, or fixedNodeIds."));
		}

		TSet<FGuid> MovedSet;
		for (const TSharedPtr<FJsonValue>& Entry : *Moved)
		{
			FGuid Guid;
			ParseNodeId(Entry->AsString(), Guid);
			MovedSet.Add(Guid);
		}
		TSet<FGuid> ScopeSet;
		for (const TSharedPtr<FJsonValue>& Entry : *Scope)
		{
			FGuid Guid;
			ParseNodeId(Entry->AsString(), Guid);
			ScopeSet.Add(Guid);
		}
		TSet<FGuid> FixedSet;
		for (const TSharedPtr<FJsonValue>& Entry : *Fixed)
		{
			FGuid Guid;
			ParseNodeId(Entry->AsString(), Guid);
			FixedSet.Add(Guid);
		}

		TSet<FGuid> ExpectedMoved;
		for (const TPair<FGuid, UEdGraphNode*>& Pair : Nodes)
		{
			const FIntPoint Target = Targets.FindChecked(Pair.Key);
			if (Target.X != Pair.Value->NodePosX || Target.Y != Pair.Value->NodePosY)
			{
				ExpectedMoved.Add(Pair.Key);
			}
		}
		if (ExpectedMoved.Num() != MovedSet.Num() || !SameGuidSet(ExpectedMoved, MovedSet))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-moved-mismatch"),
				TEXT("movedNodeIds does not exactly describe the positions that differ from the live graph."));
		}
		if (MovedSet.Num() > MaxMovedPositions)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-limit-exceeded"),
				TEXT("Layout apply accepts at most 1000 moved node positions."));
		}

		FString Mode;
		Plan->TryGetStringField(TEXT("mode"), Mode);
		for (const FGuid& Scoped : ScopeSet)
		{
			if (!Nodes.Contains(Scoped))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-scope-invalid"),
					TEXT("scopeNodeIds contains a node that does not exist on the live graph."));
			}
		}
		for (const FGuid& FixedGuid : FixedSet)
		{
			if (!Nodes.Contains(FixedGuid))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-scope-invalid"),
					TEXT("fixedNodeIds contains a node that does not exist on the live graph."));
			}
		}
		TSet<FGuid> ContentNodes;
		for (const TPair<FGuid, UEdGraphNode*>& Pair : Nodes)
		{
			if (!IsCommentClass(Pair.Value))
			{
				ContentNodes.Add(Pair.Key);
			}
		}
		if (Mode.Equals(TEXT("full"), ESearchCase::CaseSensitive))
		{
			if (FixedSet.Num() != 0)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-scope-invalid"),
					TEXT("full mode cannot contain fixed nodes."));
			}
			if (!ContainsAllGuids(ScopeSet, ContentNodes))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-scope-invalid"),
					TEXT("full mode must scope every content node; comments remain annotation-only."));
			}
		}
		else
		{
			TSet<FGuid> ExpectedFixed;
			for (const FGuid& ContentGuid : ContentNodes)
			{
				if (!ScopeSet.Contains(ContentGuid))
				{
					ExpectedFixed.Add(ContentGuid);
				}
			}
			if (!SameGuidSet(ExpectedFixed, FixedSet))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-scope-invalid"),
					TEXT("fixedNodeIds must equal every content node outside the scope."));
			}
			for (const FGuid& MovedGuid : MovedSet)
			{
				const UEdGraphNode* const* MovedNode = Nodes.Find(MovedGuid);
				if (MovedNode != nullptr
					&& !IsCommentClass(*MovedNode)
					&& !ScopeSet.Contains(MovedGuid))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-layout-scope-invalid"),
						TEXT("Every moved content node must be inside the explicit scope."));
				}
			}
		}

		// A moved comment must still cover every node it used to frame.  Those
		// nodes may have moved (including onto exactly their old coordinates),
		// so the check uses their target positions.  Apply is position-only, so
		// the live width/height are unchanged; a plan size change is never
		// applied.  Leaving any formerly contained node outside the new frame is
		// a real partial-scope corruption and stays rejected.
		for (const TPair<FGuid, UEdGraphNode*>& Pair : Nodes)
		{
			UEdGraphNode* Comment = Pair.Value;
			if (!MovedSet.Contains(Pair.Key) || !IsCommentClass(Comment))
			{
				continue;
			}
			const FIntPoint NewPosition = Targets.FindChecked(Pair.Key);
			const int32 OldLeft = Comment->NodePosX;
			const int32 OldTop = Comment->NodePosY;
			const int32 NewLeft = NewPosition.X;
			const int32 NewTop = NewPosition.Y;
			const int32 CommentWidth = FMath::Max(1, Comment->NodeWidth);
			const int32 CommentHeight = FMath::Max(1, Comment->NodeHeight);
			for (const TPair<FGuid, UEdGraphNode*>& OtherPair : Nodes)
			{
				UEdGraphNode* Other = OtherPair.Value;
				if (Other == Comment || IsCommentClass(Other))
				{
					continue;
				}
				const FIntPoint OtherTarget = Targets.FindChecked(OtherPair.Key);
				const int32 OldCenterX = Other->NodePosX + FMath::Max(1, Other->NodeWidth) / 2;
				const int32 OldCenterY = Other->NodePosY + FMath::Max(1, Other->NodeHeight) / 2;
				const int32 NewCenterX = OtherTarget.X + FMath::Max(1, Other->NodeWidth) / 2;
				const int32 NewCenterY = OtherTarget.Y + FMath::Max(1, Other->NodeHeight) / 2;
				const bool bWasContained = OldCenterX >= OldLeft
					&& OldCenterX <= OldLeft + CommentWidth
					&& OldCenterY >= OldTop
					&& OldCenterY <= OldTop + CommentHeight;
				const bool bStillContained = NewCenterX >= NewLeft
					&& NewCenterX <= NewLeft + CommentWidth
					&& NewCenterY >= NewTop
					&& NewCenterY <= NewTop + CommentHeight;
				if (bWasContained && !bStillContained)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-layout-comment-partial-scope"),
						FString::Printf(
							TEXT("Comment %s leaves node %s outside its moved frame."),
							*Pair.Key.ToString(EGuidFormats::DigitsWithHyphensLower),
							*OtherPair.Key.ToString(EGuidFormats::DigitsWithHyphensLower)));
				}
			}
		}

		// Position-only apply must not silently move a structurally unusable
		// reroute.  The same rule as the Python client gate: legacy 1->1 knots
		// stay accepted; a data fan-out or exec merge also needs an exact type
		// match and a chain that terminates at real non-knot nodes.
		for (const TPair<FGuid, UEdGraphNode*>& Pair : Nodes)
		{
			UEdGraphNode* Node = Pair.Value;
			if (!MovedSet.Contains(Pair.Key) || !IsRerouteClass(Node))
			{
				continue;
			}
			FString Why;
			if (!ValidateMovedKnot(Node, Nodes, Why))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-reroute-broken"),
					FString::Printf(
						TEXT("Reroute %s %s and cannot be moved by layout."),
						*Pair.Key.ToString(EGuidFormats::DigitsWithHyphensLower),
						*Why));
			}
		}

		TArray<UEdGraphNode*> ChangedNodes;
		TMap<FGuid, FIntPoint> BeforePositions;
		ChangedNodes.Reserve(MovedSet.Num());
		for (const TPair<FGuid, UEdGraphNode*>& Pair : Nodes)
		{
			if (MovedSet.Contains(Pair.Key))
			{
				ChangedNodes.Add(Pair.Value);
				BeforePositions.Add(Pair.Key, FIntPoint(Pair.Value->NodePosX, Pair.Value->NodePosY));
			}
		}

		if (ChangedNodes.IsEmpty())
		{
			TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
			Result->SetStringField(TEXT("action"), TEXT("apply-blueprint-graph-layout"));
			Result->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
			Result->SetStringField(TEXT("graphGuid"), Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			Result->SetNumberField(TEXT("nodeCount"), Nodes.Num());
			Result->SetNumberField(TEXT("movedNodeCount"), 0);
			Result->SetBoolField(TEXT("changed"), false);
			Result->SetBoolField(TEXT("singleTransaction"), false);
			Result->SetBoolField(TEXT("packageDirtyBefore"), false);
			Result->SetBoolField(TEXT("packageDirtyAfter"), false);
			Result->SetBoolField(TEXT("saved"), false);
			Result->SetBoolField(TEXT("compiled"), false);
			Result->SetStringField(TEXT("evidenceLevel"), TEXT("same-process"));
			Result->SetBoolField(TEXT("acceptanceEligible"), false);
			Result->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
			OutResult = Result;
			return true;
		}

		const bool bDirtyBefore = Package->IsDirty();
		const TEnumAsByte<EBlueprintStatus> BlueprintStatusBefore = Blueprint->Status;
		const bool bCachedDependenciesBefore = Blueprint->bCachedDependenciesUpToDate;
		FScopedTransaction Transaction(
			TEXT("UEAgentKitGraphLayout"),
			LOCTEXT("GraphLayoutTransaction", "UE Agent Kit Graph Layout"),
			Blueprint);
		if (!Transaction.IsOutstanding())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-transaction-unavailable"),
				TEXT("The Editor did not open an Undo transaction; no node position was changed."));
		}

		Blueprint->Modify();
		Graph->Modify();
		for (UEdGraphNode* Node : ChangedNodes)
		{
			Node->Modify();
			const FIntPoint Target = Targets.FindChecked(Node->NodeGuid);
			Node->NodePosX = Target.X;
			Node->NodePosY = Target.Y;
		}

		bool bReadbackExact = true;
		for (UEdGraphNode* Node : ChangedNodes)
		{
			const FIntPoint Target = Targets.FindChecked(Node->NodeGuid);
			if (Node->NodePosX != Target.X || Node->NodePosY != Target.Y)
			{
				bReadbackExact = false;
				break;
			}
		}

		if (!bReadbackExact)
		{
			for (UEdGraphNode* Node : ChangedNodes)
			{
				const FIntPoint* Before = BeforePositions.Find(Node->NodeGuid);
				if (Before != nullptr)
				{
					Node->NodePosX = Before->X;
					Node->NodePosY = Before->Y;
				}
			}
			Package->SetDirtyFlag(bDirtyBefore);
			Transaction.Cancel();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-readback-failed"),
				TEXT("Node positions did not read back exactly after apply; the transaction was cancelled."));
		}

		FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
		if (!Package->IsDirty())
		{
			for (UEdGraphNode* Node : ChangedNodes)
			{
				const FIntPoint* Before = BeforePositions.Find(Node->NodeGuid);
				if (Before != nullptr)
				{
					Node->NodePosX = Before->X;
					Node->NodePosY = Before->Y;
				}
			}
			Package->SetDirtyFlag(bDirtyBefore);
			Transaction.Cancel();
			Blueprint->Status = BlueprintStatusBefore;
			Blueprint->bCachedDependenciesUpToDate = bCachedDependenciesBefore;
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-dirty-not-confirmed"),
				TEXT("The Editor did not confirm the Blueprint package became dirty; the transaction was cancelled."));
		}

		TArray<TSharedPtr<FJsonValue>> Applied;
		for (UEdGraphNode* Node : ChangedNodes)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeGuid"), Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			Item->SetNumberField(TEXT("x"), Node->NodePosX);
			Item->SetNumberField(TEXT("y"), Node->NodePosY);
			Applied.Add(MakeShared<FJsonValueObject>(Item));
		}

		FString PlanId;
		FString LayoutHash;
		FString BaseGraphHash;
		Plan->TryGetStringField(TEXT("planId"), PlanId);
		Plan->TryGetStringField(TEXT("layoutHash"), LayoutHash);
		Plan->TryGetStringField(TEXT("baseGraphHash"), BaseGraphHash);

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("action"), TEXT("apply-blueprint-graph-layout"));
		Result->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		Result->SetStringField(TEXT("graphGuid"), Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		Result->SetStringField(TEXT("planId"), PlanId);
		Result->SetStringField(TEXT("layoutHash"), LayoutHash);
		Result->SetStringField(TEXT("baseGraphHash"), BaseGraphHash);
		Result->SetStringField(TEXT("graphFingerprintBefore"), CurrentFingerprint);
		Result->SetStringField(TEXT("packageRevisionBefore"), CurrentRevision);
		Result->SetNumberField(TEXT("nodeCount"), Nodes.Num());
		Result->SetNumberField(TEXT("movedNodeCount"), ChangedNodes.Num());
		Result->SetArrayField(TEXT("movedNodes"), Applied);
		Result->SetBoolField(TEXT("changed"), true);
		Result->SetBoolField(TEXT("singleTransaction"), true);
		Result->SetStringField(TEXT("transactionTitle"), TEXT("UE Agent Kit Graph Layout"));
		Result->SetBoolField(TEXT("packageDirtyBefore"), bDirtyBefore);
		Result->SetBoolField(TEXT("packageDirtyAfter"), Package->IsDirty());
		Result->SetBoolField(TEXT("saved"), false);
		Result->SetBoolField(TEXT("compiled"), false);
		Result->SetStringField(TEXT("evidenceLevel"), TEXT("same-process"));
		Result->SetBoolField(TEXT("acceptanceEligible"), false);
		Result->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
		OutResult = Result;
		return true;
	}

	bool TryApply(
		const TSharedPtr<FJsonObject>& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (GEditor == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-editor-unavailable"),
				TEXT("The Unreal Editor is unavailable."));
		}
		if (GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-pie-active"),
				TEXT("Graph layout apply is unavailable while PIE or SIE is active."));
		}

		FString AssetPath;
		FString GraphGuidText;
		if (!Request.IsValid()
			|| !Request->TryGetStringField(TEXT("assetPath"), AssetPath)
			|| !Request->TryGetStringField(TEXT("graphGuid"), GraphGuidText))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-invalid-parameters"),
				TEXT("The request must contain exact assetPath and graphGuid fields."));
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		if (!ResolveExactTarget(AssetPath, GraphGuidText, Blueprint, Graph, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		UAssetEditorSubsystem* AssetEditorSubsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
		if (AssetEditorSubsystem == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-editor-unavailable"),
				TEXT("The Asset Editor Subsystem is unavailable."));
		}
		IAssetEditorInstance* Instance = AssetEditorSubsystem->FindEditorForAsset(Blueprint, false);
		if (Instance == nullptr || Instance->GetEditorName() != FName(TEXT("BlueprintEditor")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-editor-not-open"),
				TEXT("Open the exact Blueprint in a Blueprint Editor before applying layout."));
		}
		FBlueprintEditor* BlueprintEditor = static_cast<FBlueprintEditor*>(Instance);
		if (BlueprintEditor->GetBlueprintObj() != Blueprint)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-editor-mismatch"),
				TEXT("The Blueprint Editor does not own the exact requested Blueprint."));
		}
		if (BlueprintEditor->GetFocusedGraph() != Graph)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-not-focused"),
				TEXT("Open and focus the exact target graph before applying layout."));
		}
		if (!BlueprintEditor->IsEditable(Graph))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-not-editable"),
				TEXT("The focused graph is not editable in the current Editor state."));
		}
		if (Cast<UEdGraphSchema_K2>(Graph->GetSchema()) == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-graph-not-k2"),
				TEXT("Layout apply currently accepts only an ordinary K2 Blueprint graph."));
		}

		return ApplyToGraph(Blueprint, Graph, Request, OutResult, OutErrorCode, OutErrorMessage);
	}
}

#if WITH_DEV_AUTOMATION_TESTS
namespace
{
	TSharedPtr<FJsonObject> MakeGateTestPlan()
	{
		TSharedRef<FJsonObject> Plan = MakeShared<FJsonObject>();
		Plan->SetStringField(TEXT("schemaVersion"), TEXT("ueak.layoutplan/1.0"));
		Plan->SetStringField(TEXT("planId"), TEXT("layout:test"));
		Plan->SetStringField(TEXT("baseGraphHash"), TEXT("sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
		Plan->SetStringField(TEXT("layoutHash"), TEXT("sha256:9851d00e14f91fb25bc859160a24b6f64cd8a9a593b2ae17c42a8f6f632392a6"));
		Plan->SetStringField(TEXT("mode"), TEXT("full"));
		Plan->SetStringField(TEXT("status"), TEXT("ok"));
		Plan->SetStringField(TEXT("errorCode"), TEXT(""));
		Plan->SetStringField(TEXT("evidenceLevel"), TEXT("same-process"));
		Plan->SetBoolField(TEXT("acceptanceEligible"), false);
		Plan->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));

		auto MakePosition = [](const TCHAR* GuidText, const double X, const double Y)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeId"), FString(TEXT("n:")) + GuidText);
			Item->SetNumberField(TEXT("x"), X);
			Item->SetNumberField(TEXT("y"), Y);
			TSharedRef<FJsonObject> Size = MakeShared<FJsonObject>();
			Size->SetNumberField(TEXT("width"), 100.0);
			Size->SetNumberField(TEXT("height"), 40.0);
			Size->SetStringField(TEXT("source"), TEXT("stored"));
			Item->SetObjectField(TEXT("size"), Size);
			return MakeShared<FJsonValueObject>(Item);
		};

		const TCHAR* First = TEXT("00000001-0000-4000-8000-000000000001");
		const TCHAR* Second = TEXT("00000002-0000-4000-8000-000000000002");
		TArray<TSharedPtr<FJsonValue>> PositionsArray;
		PositionsArray.Add(MakePosition(First, 0.0, 0.0));
		PositionsArray.Add(MakePosition(Second, 200.0, 0.0));
		Plan->SetArrayField(TEXT("positions"), PositionsArray);

		TArray<TSharedPtr<FJsonValue>> MovedArray;
		MovedArray.Add(MakeShared<FJsonValueString>(FString(TEXT("n:")) + First));
		Plan->SetArrayField(TEXT("movedNodeIds"), MovedArray);

		TArray<TSharedPtr<FJsonValue>> ScopeArray;
		ScopeArray.Add(MakeShared<FJsonValueString>(FString(TEXT("n:")) + First));
		ScopeArray.Add(MakeShared<FJsonValueString>(FString(TEXT("n:")) + Second));
		Plan->SetArrayField(TEXT("scopeNodeIds"), ScopeArray);
		const TArray<TSharedPtr<FJsonValue>> EmptyArray;
		Plan->SetArrayField(TEXT("fixedNodeIds"), EmptyArray);
		return Plan;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitGraphLayoutPlanGateTest,
	"UEAgentKit.GraphLayout.PlanGateContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitGraphLayoutPlanGateTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	TArray<FString> Errors;
	const TSharedPtr<FJsonObject> Plan = MakeGateTestPlan();
	TestTrue(TEXT("valid plan passes shape gate"), UEAgentKitBlueprintGraphLayout::ValidatePlanShape(Plan, Errors));

	const TSharedPtr<FJsonObject> BadLayoutHash = MakeGateTestPlan();
	BadLayoutHash->SetStringField(TEXT("layoutHash"), TEXT("sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"));
	TestFalse(TEXT("tampered layoutHash is rejected"), UEAgentKitBlueprintGraphLayout::ValidatePlanShape(BadLayoutHash, Errors));
	TestTrue(TEXT("layoutHash mismatch is reported"), Errors.Contains(TEXT("graph-layout-layout-hash-mismatch")));

	const TSharedPtr<FJsonObject> Duplicate = MakeGateTestPlan();
	const TArray<TSharedPtr<FJsonValue>>* Positions = nullptr;
	Duplicate->TryGetArrayField(TEXT("positions"), Positions);
	if (Positions != nullptr)
	{
		TArray<TSharedPtr<FJsonValue>> DuplicatePositions;
		DuplicatePositions.Add((*Positions)[0]);
		DuplicatePositions.Add((*Positions)[0]);
		Duplicate->SetArrayField(TEXT("positions"), DuplicatePositions);
	}
	TestFalse(TEXT("duplicate node position is rejected"), UEAgentKitBlueprintGraphLayout::ValidatePlanShape(Duplicate, Errors));
	TestTrue(TEXT("duplicate reports duplicate"), Errors.Contains(TEXT("graph-layout-duplicate-node")));

	const TSharedPtr<FJsonObject> OutOfRange = MakeGateTestPlan();
	TSharedRef<FJsonObject> OverflowItem = MakeShared<FJsonObject>();
	OverflowItem->SetStringField(TEXT("nodeId"), TEXT("n:00000001-0000-4000-8000-000000000001"));
	OverflowItem->SetNumberField(TEXT("x"), 2147483648.0);
	OverflowItem->SetNumberField(TEXT("y"), 0.0);
	TSharedRef<FJsonObject> OverflowSize = MakeShared<FJsonObject>();
	OverflowSize->SetNumberField(TEXT("width"), 100.0);
	OverflowSize->SetNumberField(TEXT("height"), 40.0);
	OverflowSize->SetStringField(TEXT("source"), TEXT("stored"));
	OverflowItem->SetObjectField(TEXT("size"), OverflowSize);
	TArray<TSharedPtr<FJsonValue>> OverflowPositions;
	OverflowPositions.Add(MakeShared<FJsonValueObject>(OverflowItem));
	OutOfRange->SetArrayField(TEXT("positions"), OverflowPositions);
	TestFalse(TEXT("int32 overflow is rejected"), UEAgentKitBlueprintGraphLayout::ValidatePlanShape(OutOfRange, Errors));
	TestTrue(TEXT("overflow reports coordinate"), Errors.Contains(TEXT("graph-layout-coordinate-out-of-range")));

	const TSharedPtr<FJsonObject> Limit = MakeGateTestPlan();
	TArray<TSharedPtr<FJsonValue>> TooManyMoved;
	for (int32 Index = 0; Index < 1001; ++Index)
	{
		TooManyMoved.Add(MakeShared<FJsonValueString>(
			FString::Printf(TEXT("n:00000000-0000-4000-8000-%012x"), Index + 1)));
	}
	Limit->SetArrayField(TEXT("movedNodeIds"), TooManyMoved);
	TestFalse(TEXT("more than 1000 moved positions is rejected"), UEAgentKitBlueprintGraphLayout::ValidatePlanShape(Limit, Errors));
	TestTrue(TEXT("limit reports exceeded"), Errors.Contains(TEXT("graph-layout-limit-exceeded")));

	const TSharedPtr<FJsonObject> Rejected = MakeGateTestPlan();
	Rejected->SetStringField(TEXT("status"), TEXT("rejected"));
	TestFalse(TEXT("rejected plan is rejected"), UEAgentKitBlueprintGraphLayout::ValidatePlanShape(Rejected, Errors));
	TestTrue(TEXT("rejected status is reported"), Errors.Contains(TEXT("graph-layout-plan-rejected")));
	return true;
}
#endif

#undef LOCTEXT_NAMESPACE
